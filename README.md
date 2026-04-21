# Psi Agent Platform

一个绿色可移植、组件化的 AI Agent 平台。

## 特性

- **绿色可移植**: 一个 workspace 目录包含完整的 agent（提示词、工具、技能）
- **组件化**: 四个独立进程通过 Unix socket 通信
- **Let it crash**: 除了网络故障，所有错误都应该让进程 crash
- **不考虑兼容性**: 目前是第一版，不需要向后兼容

## 架构

```
┌─────────────┐     socket      ┌─────────────┐     socket      ┌─────────────┐
│ psi-channel │ ─────────────── │ psi-session │ ─────────────── │   psi-ai    │
│     (TUI)   │                 │  (ReAct)    │                 │  (LLM API)  │
└─────────────┘                 └─────────────┘                 └─────────────┘
                                        │
                                        ▼
                                ┌─────────────┐
                                │  workspace  │
                                │ (tools/skills)
                                └─────────────┘
```

**Socket 命名约定:**
- 代码变量：`session_socket`, `channel_socket`, `ai_socket`
- 文件命名：`ai.sock`, `channel.sock`

## 安装

```bash
uv sync
```

开发依赖：

```bash
uv sync --all-extras
```

## 开发

本仓库使用 **ruff** (lint/格式化) 和 **ty** (类型检查) 进行代码质量控制。

**Lint 检查:**
```bash
uv run ruff check examples/ tests/ src/
```

**格式检查:**
```bash
uv run ruff format examples/ tests/ src/ --check
```

**类型检查:**
```bash
uv run ty check examples/ tests/ src/
```

**运行测试:**
```bash
# 单元测试
uv run pytest tests/unit/ -v

# 集成测试（需要设置环境变量）
export OPENAI_API_KEY="your-api-key"
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_MODEL="gpt-4o-mini"
uv run pytest tests/integration/ -v
```

**CI/CD:** GitHub Actions 自动运行 lint、类型检查和单元测试。集成测试需要配置 GitHub Secrets（`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`）。

## 快速开始

**终端 1 - 启动 LLM Caller:**
```bash
uv run psi-ai-openai --session-socket ./ai.sock \
    --model gpt-4o \
    --api-key $API_KEY \
    --base-url https://api.openai.com/v1
```

**终端 2 - 启动 Session:**
```bash
# 默认：自动生成 session_id，无历史记录
uv run psi-session --workspace ./examples/simple_example \
    --channel-socket ./channel.sock \
    --ai-socket ./ai.sock

# 指定 session_id：继续历史记录
uv run psi-session --workspace ./examples/simple_example \
    --channel-socket ./channel.sock \
    --ai-socket ./ai.sock \
    --session-id main
```

**终端 3 - 启动 TUI:**
```bash
uv run psi-channel-tui --session-socket ./channel.sock
```

## 模块

| 模块 | 说明 | Python API |
|------|------|------------|
| `psi-ai-openai` | LLM Caller（OpenAI 兼容） | `run_ai()` |
| `psi-session` | ReAct 循环引擎 | `run_session()` |
| `psi-channel-tui` | TUI 用户界面 | `run_channel()` |
| `psi-workspace-create` | 从目录创建 SquashFS | `run_create()` |
| `psi-workspace-mount` | 挂载 SquashFS 为 workspace（无需 root） | `run_mount()` |
| `psi-workspace-umount` | 卸载 workspace（无需 root） | `run_umount()` |
| `psi-workspace-snapshot` | 创建快照 | `run_snapshot()` |

**Workspace 依赖（Ubuntu/Debian）:**
```bash
sudo apt install squashfuse fuse-overlayfs squashfs-tools
```

所有 CLI 使用 **tyro** 实现，支持 `--log-level` 参数控制日志输出。TUI 默认 `WARNING` 级别避免干扰界面。

## Python API

可以直接在 Python 代码中使用：

```python
import asyncio
from psi_session import run_session
from psi_ai.openai import run_ai
from psi_workspace import run_create, run_mount, run_umount, run_snapshot

async def main():
    # 创建初始 squashfs
    await run_create("./examples/simple_example", "base.sqfs", tag="base")
    
    # 挂载
    await run_mount("base.sqfs", "./workspace")
    
    # 修改 workspace...
    
    # 创建快照
    await run_snapshot("./workspace", "v2.sqfs", tag="v1")
    
    # 卸载
    await run_umount("./workspace")
    
asyncio.run(main())
```

## Workspace 结构

```
workspace/
├── AGENT.md           # 身份描述
├── tools/*.py         # 工具（导出 async run(params, workspace_path))
├── skills/*/SKILL.md  # 技能
└── systems/builder.py # 系统提示词构造器
```

## 文档

- [SPEC.md](SPEC.md) - 详细规格说明书
- [CLAUDE.md](CLAUDE.md) - 给 Claude 的项目说明

## License

AGPLv3