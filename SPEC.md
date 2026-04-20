# Psi Agent 平台规格说明书

## 1. 总体架构与设计原则

**绿色可移植**：一个智能体（Agent）的所有内容（提示词、工具、技能、配置）全部存放在一个独立的 workspace 目录中。该目录可以整体复制、移动、版本控制。

**组件化**：系统由四个独立模块组成，通过 Unix Domain Socket 通信：

- **psi-ai-openai**：LLM Caller（OpenAI 兼容），封装大语言模型 API，通过 named socket 暴露接口。
- **psi-session**：运行 ReAct 循环，管理对话历史，调用工具/技能。
- **psi-channel-tui**：TUI 用户交互界面。
- **psi-workspace**：SquashFS/OverlayFS 管理器，负责镜像挂载和快照。

**Let it crash**：组件出错时不做复杂恢复，让进程崩溃。简化实现，依赖外部重启机制。

## 2. 模块详细定义

### 2.1 psi-ai-openai

职责：
- 监听 named socket，接收 OpenAI Chat Completion 请求格式 JSON。
- 转发给真实 LLM API（OpenAI、阿里云 DashScope 等），返回 OpenAI 格式响应。
- 支持流式和非流式模式。

启动：
```bash
psi-ai-openai --socket /tmp/llm.sock \
              --model gpt-4o \
              --api-key $KEY \
              --base-url https://api.openai.com/v1
```

协议：
- 请求/响应均为单行 JSON，末尾换行。
- 流式：每个 chunk 一行，最后发送 `{"id": ..., "done": true}`。

### 2.2 psi-session

职责：
- 加载 workspace 目录。
- 维护对话历史（SQLite 存储）。
- 运行 ReAct 循环：调用 LLM → 执行工具 → 返回结果。
- 不负责模型选择、长期记忆管理。

启动：
```bash
psi-session --workspace ./workspace \
            --channel-socket /tmp/channel.sock \
            --llm-socket /tmp/llm.sock \
            --session-id main
```

SQLite Schema：
```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    content TEXT,
    tool_calls TEXT,      -- JSON
    tool_call_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

每个 session 有独立的 SQLite 文件：`state/session-{id}.db`。

### 2.3 psi-channel-tui

职责：
- 提供终端 TUI 界面。
- 连接 session socket，发送用户消息，接收回复。
- 不实现任何内部命令，退出通过 Ctrl+C。

启动：
```bash
psi-channel-tui --session-socket /tmp/channel.sock
```

### 2.4 psi-workspace

职责：
- 挂载 SquashFS 镜像为可写 workspace（使用 OverlayFS）。
- 创建快照（将修改打包为新 SquashFS）。
- 保留快照历史（manifest.json）。

启动：
```bash
psi-workspace mount agent.sqfs ./workspace
psi-workspace snapshot ./workspace --output new.sqfs --description "v2"
psi-workspace unmount ./workspace
psi-workspace list ./workspace
```

OverlayFS 目录结构：
```
workspace/           # 挂载点（Agent 看到的）
workspace.lower/     # SquashFS 挂载（只读）
workspace.upper/     # 可写层（修改存这里）
workspace.work/      # OverlayFS work 目录
manifest.json        # 快照历史
```

## 3. 通信协议

### 3.1 Session ↔ LLM Caller

请求（Session → AI）：
```json
{
  "id": "req-1",
  "messages": [...],
  "tools": [...],
  "tool_choice": "auto",
  "stream": true
}
```

流式响应（AI → Session）：
```json
{"id": "req-1", "choices": [{"delta": {"content": "text"}}]}
{"id": "req-1", "choices": [{"delta": {"tool_calls": [...]}}]}
{"id": "req-1", "done": true}
```

非流式响应：
```json
{"id": "req-1", "choices": [{"message": {"role": "assistant", "content": "...", "tool_calls": [...]}}]}
```

### 3.2 Session ↔ Channel

用户消息（Channel → Session）：
```json
{"role": "user", "content": "用户输入"}
```

响应（Session → Channel）：
```json
{"role": "assistant", "content": "回复内容"}
```

工具调用不暴露给 Channel，只返回最终文本。

## 4. Workspace 目录结构

```
workspace/
├── AGENT.md               # 身份描述
├── tools/                 # 工具目录
│   └── *.py               # 每个文件导出 async run(params, workspace_path)
├── skills/                # 技能目录
│   └── <name>/SKILL.md    # 带 frontmatter 的技能说明
├── systems/               # 系统提示词构造器
│   └── builder.py         # 导出 build_system_prompt 和 trim_history
├── state/                 # Session 状态
│   └── session-{id}.db    # SQLite 历史
└── schedules/             # 定时任务（可选）
    └── *.md               # 带 cron frontmatter
```

## 4.1 内置工具

示例 workspace 提供两个工具：

- **read_file**: 读取文件内容
- **bash**: 执行 shell 命令（无安全检查，完整 bash 权限）

## 4.2 项目目录结构

```
psi/
├── pyproject.toml         # uv 项目配置
├── SPEC.md                 # 规格说明书
├── CLAUDE.md               # 给 Claude 的项目说明
├── .gitignore
├── src/
│   ├── psi_session/       # ReAct 循环引擎
│   ├── psi_channel/       # Channel 入口
│   │   └── tui/           # TUI 实现
│   ├── psi_ai/            # AI 入口
│   │   └── openai/        # OpenAI 兼容实现
│   ├── psi_workspace/     # SquashFS/OverlayFS 管理器
│   └── psi_common/        # 共享协议定义
└── examples/
    └── simple_example/    # 简单示例 workspace
        ├── AGENT.md
        ├── tools/
        │   ├── read_file.py
        │   └── bash.py
        ├── skills/
        └── systems/
```

## 5. 工具规范

```python
async def run(params: dict, workspace_path: str) -> dict:
    """工具描述。参数说明在 docstring 中。

    Args:
        param1: 参数1描述
        param2: 参数2描述（可选）
    """
    # 返回必须包含 success
    return {"success": True, "content": "..."}  # 或
    return {"success": False, "error": "..."}
```

参数 schema 通过 inspect.signature + docstring 自动生成。

## 6. 系统提示词构造器

```python
async def build_system_prompt(context: dict) -> str:
    """
    context 包含:
      - workspace_path: str
      - skills_index: list[{"name": str, "description": str}]
      - current_time: str (ISO 格式)
      - history: list[dict]
    返回: 系统提示词字符串
    """

async def trim_history(messages: list, limit: int) -> list:
    """
    当历史超过限制时压缩/截断。
    返回: 新的 messages 列表
    """
```

## 7. ReAct 循环

1. 接收用户消息，追加到 history，存入 SQLite。
2. 调用 `build_system_prompt()` 和 `trim_history()`。
3. 向 AI 发送请求（含 system + trimmed history + tools）。
4. 收到响应：
   - 有 tool_calls：执行工具，追加 tool 消息，继续循环。
   - 无 tool_calls：追加 assistant 消息，返回文本给 Channel。
5. 循环上限：10 次。

## 8. 快照历史

manifest.json：
```json
{
  "current": {
    "name": "base.sqfs",
    "status": "mounted",
    "mounted_at": "2026-04-20T10:00:00"
  },
  "snapshots": [
    {"name": "v1.sqfs", "description": "first", "created_at": "..."},
    {"name": "v2.sqfs", "description": "added tool", "created_at": "..."}
  ]
}
```

类似 Docker 层级结构，每个快照基于父镜像。

## 9. 不实现的功能

- 流式输出给 Channel（第一版）
- 定时任务（第一版）
- 内置记忆系统
- 工具沙箱
- 热更新 tools/skills
- 复杂错误恢复

## 10. 日志

所有模块（除 psi-channel-tui）使用 loguru 统一日志：

```
2026-04-20 18:03:42 | INFO     | session | Session initialized | id=test | workspace=/path
```

格式：`时间 | 级别 | 模块名 | 消息 | 键值对`

可通过 `--log-level DEBUG/INFO/WARNING/ERROR` 控制日志级别。

## 11. 依赖

- Python 3.10+
- openai（SDK）
- aiosqlite（异步 SQLite）
- prompt-toolkit（TUI）
- loguru（日志）
- SquashFS/OverlayFS（系统支持）