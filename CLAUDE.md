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

除 TUI 外，所有模块使用 loguru：
```
2026-04-20 18:03:42 | INFO | session | Session initialized | id=test
```

控制级别：`--log-level DEBUG/INFO/WARNING/ERROR`

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

## 详细规格

见 `SPEC.md`。