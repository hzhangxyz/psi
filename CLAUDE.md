# Psi Agent Platform

一个绿色可移植的 AI Agent 平台，由独立的模块通过 Unix socket 通信组成。

## 快速开始

```bash
# 1. 启动 LLM Caller
uv run psi-ai-openai --session-socket ./ai.sock \
    --model qwen3.5-plus \
    --api-key $API_KEY \
    --base-url https://coding.dashscope.aliyuncs.com/v1

# 2. 启动 Session
uv run psi-session --workspace ./examples/simple_example \
    --channel-socket ./channel.sock \
    --ai-socket ./ai.sock

# 3. 启动 TUI
uv run psi-channel-tui --session-socket ./channel.sock
```

## 项目结构

```
src/
├── psi_session/        # ReAct 循环引擎（核心）
├── psi_channel/tui/    # TUI 用户界面
├── psi_ai/openai/      # OpenAI 兼容 LLM Caller
├── psi_workspace/      # SquashFS/OverlayFS 管理器
└── psi_common/         # 共享协议

examples/
└── simple_example/     # 示例 workspace
```

## 核心概念

### Workspace

一个 workspace 是一个完整的 agent，包含：
- `AGENT.md`: 身份描述
- `tools/`: 工具（每个 .py 导出 `async run(params, workspace_path)`)
- `skills/`: 技能（SKILL.md 格式）
- `systems/builder.py`: 系统提示词构造器

### 通信协议

所有模块通过 Unix socket 通信，使用 JSON Lines 格式：

- Session ↔ AI: OpenAI Chat Completion 格式（流式）
- Session ↔ Channel: `{"role": "user/assistant", "content": "..."}`

### ReAct 循环

Session 接收用户消息后：
1. 构建 system prompt（通过 builder.py）
2. 调用 LLM
3. 如果有 tool_calls → 执行工具 → 继续循环
4. 如果无 tool_calls → 返回文本给 Channel

## 设计原则

- **Let it crash**: 除了网络故障，所有错误都应该让进程 crash
  - 网络故障（可优雅处理）：Connection、Broken pipe、Pipe error
  - 其他错误（应该 crash）：JSON 解析错误、API 错误、业务逻辑错误
- **绿色可移植**: workspace 可整体复制/移动
- **组件化**: 独立进程，socket 通信
- **不考虑兼容性**: 目前是第一版，不需要向后兼容，可直接删除旧代码

## 日志

所有模块使用 loguru：
```
2026-04-20 18:03:42 | INFO | session | Session initialized | id=test
```

控制级别：`--log-level DEBUG/INFO/WARNING/ERROR`

**TUI 特殊处理**：默认 `WARNING` 级别，避免日志干扰界面。

## 数据模型

使用 pydantic BaseModel：
- `LLMRequest`/`LLMResponse`: LLM 通信
- `ToolResult`: 工具结果
- `SnapshotEntry`/`Manifest`: 快照元数据

## 开发

本仓库使用 **ruff** (lint/格式化) 和 **ty** (类型检查) 进行代码质量控制。

```bash
# Lint 检查
uv run ruff check examples/ tests/ src/
uv run ruff check --fix examples/ tests/ src/

# 格式检查
uv run ruff format examples/ tests/ src/ --check

# 类型检查
uv run ty check examples/ tests/ src/

# 测试
uv run pytest tests/unit/ -v

# 集成测试（需要设置环境变量）
export OPENAI_API_KEY="your-api-key"
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_MODEL="gpt-4o-mini"
uv run pytest tests/integration/ -v
```

**CI/CD**: GitHub Actions 自动运行 ruff check、ruff format、ty 和测试（`.github/workflows/test.yml`）。

**CLI**: 所有命令使用 tyro 实现，参数通过 dataclass 定义。

## 常用命令

```bash
# 安装依赖
uv sync

# 运行测试
uv run psi-session --workspace ./examples/simple_example ...

# workspace 管理（需要 root）
# Workspace 管理（使用 FUSE，无需 root）
# 先安装依赖: sudo apt install squashfuse fuse-overlayfs squashfs-tools
psi-workspace-create ./examples/simple_example base.sqfs
psi-workspace-mount base.sqfs ./workspace
psi-workspace-snapshot ./workspace --output new.sqfs
```

## Python API

所有模块同时提供 Python function 接口：

```python
from psi_session import run_session
from psi_ai.openai import run_ai
from psi_channel.tui import run_channel
from psi_workspace import run_mount, run_unmount, run_snapshot, run_list

# 启动 AI Caller
await run_ai(
    session_socket="./ai.sock",
    model="qwen3.5-plus",
    api_key="...",
    base_url="https://coding.dashscope.aliyuncs.com/v1",
    log_level="INFO"
)

# 启动 Session
await run_session(
    workspace_path="./examples/simple_example",
    channel_socket="./channel.sock",
    ai_socket="./ai.sock",
    session_id="main",
    log_level="INFO"
)

# 启动 TUI
await run_channel(session_socket="./channel.sock")

# Workspace 管理（需要 root）
await run_mount("agent.sqfs", "./workspace")
await run_unmount("./workspace")
await run_snapshot("./workspace", "new.sqfs", description="v2")
run_list("./workspace")
```

## 详细规格

见 `SPEC.md`。

## 代码风格

### 类设计

- **内部状态用私有属性**: 类内部状态用 `_` 前缀（如 `_messages`, `_tools`, `_workspace_path`）
- **配置用 Pydantic**: 配置参数封装在 `SessionConfig` 等 Pydantic model 中
- **配置与实现分离**: 配置类只存参数，业务类接收配置对象

### 函数设计

- **辅助函数独立**: 通用辅助函数独立定义（如 `_is_valid_tool_call_name`, `_load_python_module`)
- **函数有单一职责**: 每个函数只做一件事，名字描述职责
- **缓存复用**: 重复加载的资源缓存（如 `_builder_module`）

### 代码组织

- **模块顶部 import**: 所有 import 放文件顶部，不在函数内部 import
- **类型注解**: 所有函数参数和返回值有类型注解
- **docstring 简洁**: docstring 只说明功能，不写冗余说明

### 日志风格

- **格式统一**: `logger.info("Action | key={value}")` 格式，用 `|` 分隔
- **级别合理**: 
  - `INFO`: 重要状态变化（初始化、连接、完成）
  - `DEBUG`: 详细过程（参数、中间状态）
  - `WARNING`: 预期内的异常情况
  - `ERROR`: 错误和失败

### 其他约定

- **避免 ad-hoc**: 不写重复逻辑，提取为辅助函数
- **f-string 有变量**: f-string 必须有插值，否则用普通字符串
- **is 比较**: `True/False/None` 用 `is` 比较，不用 `==`

### 命名风格

#### CLI 参数命名

- **snake_case**: 所有 CLI 参数使用 snake_case（如 `channel_socket`, `log_level`）
- **位置参数**: 简短单词（如 `workspace`, `model`, `output`）
- **socket 参数**: `*_socket` 格式（如 `channel_socket`, `ai_socket`, `session_socket`）
- **log_level**: 所有模块统一有 `log_level` 参数

#### 文件命名

- **模块目录**: `psi_<name>` 格式（如 `psi_session`, `psi_ai`, `psi_channel`, `psi_workspace`）
- **Python 文件**: snake_case（如 `builder.py`, `protocol.py`）
- **配置文件**: 小写无扩展名约定（如 `AGENT.md`, `SKILL.md`）

#### 变量命名

- **私有属性**: `_` 前缀（如 `_messages`, `_tools`, `_config`）
- **常量**: UPPER_CASE 或 snake_case（如 `MAX_ITERATIONS` 或 `max_iterations`）
- **函数**: snake_case（如 `load_tools`, `build_system_prompt`）
- **类**: PascalCase（如 `Session`, `SessionConfig`, `WorkspaceManager`）

#### Socket 命名

**变量命名（代码中）:**
- `session_socket`: AI Caller 监听的 socket（Session 连接到此）
- `channel_socket`: Session 监听的 socket（Channel 连接到此）
- `ai_socket`: Session 连接 AI 的 socket（与 AI 的 `session_socket` 对应）

**文件命名（示例和测试）:**
- `ai.sock`: AI Caller socket 文件
- `channel.sock`: Channel/Session socket 文件

**路径原则:**
- 生产环境：相对路径（如 `./channel.sock`, `./ai.sock`）
- 测试环境：pytest `tmp_path` fixture（如 `tmp_path / "channel.sock"`）

#### 数据库/JSON 字段

- **snake_case**: 与 Python 保持一致（如 `session_id`, `created_at`）