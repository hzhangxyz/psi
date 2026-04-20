# Psi Agent Platform

一个绿色可移植、组件化的 AI Agent 平台。

## 特性

- **绿色可移植**: 一个 workspace 目录包含完整的 agent（提示词、工具、技能）
- **组件化**: 四个独立进程通过 Unix socket 通信
- **Let it crash**: 简单设计，不做复杂错误恢复

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

## 安装

```bash
uv sync
```

开发依赖：

```bash
uv sync --all-extras
```

## 开发

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
uv run pytest tests/ -v
```

**CI/CD:** GitHub Actions 自动运行 ruff check、ruff format、ty 类型检查和测试（`.github/workflows/test.yml`）。

## 快速开始

**终端 1 - 启动 LLM Caller:**
```bash
uv run psi-ai-openai --socket ./psi-ai.sock \
    --model gpt-4o \
    --api-key $API_KEY \
    --base-url https://api.openai.com/v1
```

**终端 2 - 启动 Session:**
```bash
uv run psi-session --workspace ./examples/simple_example \
    --channel-socket ./channel.sock \
    --llm-socket ./psi-ai.sock
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
| `psi-workspace` | SquashFS/OverlayFS 管理器 | `run_mount()`, `run_unmount()`, `run_snapshot()`, `run_list()` |

所有 CLI 使用 **tyro** 实现，支持 `--log-level` 参数控制日志输出。TUI 默认 `WARNING` 级别避免干扰界面。

## Python API

可以直接在 Python 代码中使用：

```python
import asyncio
from psi_session import run_session
from psi_ai.openai import run_ai

async def main():
    await run_ai(
        socket="./ai.sock",
        model="gpt-4o",
        api_key="...",
        base_url="https://api.openai.com/v1"
    )
    
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