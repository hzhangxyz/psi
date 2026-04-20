# Psi Agent Platform

一个绿色可移植的 AI Agent 平台，由独立的模块通过 Unix socket 通信组成。

## 快速开始

```bash
# 1. 启动 LLM Caller
uv run psi-ai-openai --socket ./psi-ai.sock \
    --model qwen3.5-plus \
    --api-key $API_KEY \
    --base-url https://coding.dashscope.aliyuncs.com/v1

# 2. 启动 Session
uv run psi-session --workspace ./examples/simple_example \
    --channel-socket ./channel.sock \
    --llm-socket ./psi-ai.sock

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

- **Let it crash**: 错误时不做复杂恢复
- **绿色可移植**: workspace 可整体复制/移动
- **组件化**: 独立进程，socket 通信

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

```bash
# Lint 检查
uv run ruff check examples/ tests/ src/
uv run ruff check --fix examples/ tests/ src/

# 格式检查
uv run ruff format examples/ tests/ src/ --check

# 类型检查
uv run ty check examples/ tests/ src/

# 测试
uv run pytest tests/ -v
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
psi-workspace mount agent.sqfs ./workspace
psi-workspace snapshot ./workspace --output new.sqfs
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
    socket_path="./ai.sock",
    model="qwen3.5-plus",
    api_key="...",
    base_url="https://coding.dashscope.aliyuncs.com/v1",
    log_level="INFO"
)

# 启动 Session
await run_session(
    workspace_path="./examples/simple_example",
    channel_socket="./channel.sock",
    llm_socket="./ai.sock",
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