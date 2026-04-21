"""Psi Session - Core ReAct loop engine."""

import asyncio
import importlib.util
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite
import tyro
from loguru import logger
from pydantic import BaseModel

from psi_common import LLMRequest, ToolResult


class SessionConfig(BaseModel):
    """Session configuration."""

    workspace_path: str
    channel_socket: str
    ai_socket: str
    session_id: str = "default"
    max_iterations: int = 10


def _is_valid_tool_call_name(name: Any) -> bool:
    """Check if tool call name is valid."""
    if not name:
        return False
    name_str = str(name)
    return name_str not in ("", "None", "null") and not name_str.startswith("\x00")


def _load_python_module(path: Path, module_name: str) -> Any:
    """Load a Python module from file path."""
    spec = importlib.util.spec_from_file_location(module_name, path)
    if not spec or not spec.loader:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Session:
    """Core session that runs the ReAct loop."""

    def __init__(self, config: SessionConfig) -> None:
        self._config = config
        self._workspace_path = Path(config.workspace_path).resolve()
        self._db_path = self._workspace_path / "state" / f"session-{config.session_id}.db"
        self._messages: list[dict[str, Any]] = []
        self._tools: dict[str, Callable[..., Awaitable[dict[str, Any]]]] = {}
        self._tools_schema: list[dict[str, Any]] = []
        self._skills_index: list[dict[str, str]] = []
        self._builder_module: Any = None

        logger.info(f"Session initialized | id={config.session_id} | workspace={self._workspace_path}")
        logger.debug(f"Session config | channel={config.channel_socket} | ai={config.ai_socket}")

    async def init_db(self) -> None:
        """Initialize SQLite database for message history."""
        db_dir = self._db_path.parent
        db_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Creating database directory | path={db_dir}")

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT,
                    tool_calls TEXT,
                    tool_call_id TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.commit()

        logger.info(f"Database initialized | path={self._db_path}")

    async def load_history(self) -> None:
        """Load message history from database."""
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute("""
                SELECT role, content, tool_calls, tool_call_id FROM messages ORDER BY id
            """)
            rows = await cursor.fetchall()

        for row in rows:
            msg: dict[str, Any] = {"role": row[0]}
            if row[1]:
                msg["content"] = row[1]
            if row[2]:
                msg["tool_calls"] = json.loads(row[2])
            if row[3]:
                msg["tool_call_id"] = row[3]
            self._messages.append(msg)

        logger.info(f"History loaded | count={len(self._messages)} messages")

    async def save_message(self, message: dict[str, Any]) -> None:
        """Save a message to database."""
        tool_calls_json = json.dumps(message.get("tool_calls")) if message.get("tool_calls") else None
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO messages (role, content, tool_calls, tool_call_id)
                VALUES (?, ?, ?, ?)
            """,
                (message["role"], message.get("content"), tool_calls_json, message.get("tool_call_id")),
            )
            await db.commit()

        logger.debug(f"Message saved | role={message['role']}")

    def _load_builder_module(self) -> Any:
        """Load systems/builder.py module (cached)."""
        if self._builder_module is not None:
            return self._builder_module

        builder_path = self._workspace_path / "systems" / "builder.py"
        if not builder_path.exists():
            return None

        self._builder_module = _load_python_module(builder_path, "builder")
        return self._builder_module

    def load_tools(self) -> None:
        """Load tools from workspace/tools/ directory."""
        tools_dir = self._workspace_path / "tools"
        if not tools_dir.exists():
            logger.warning(f"Tools directory not found | path={tools_dir}")
            return

        for tool_file in tools_dir.glob("*.py"):
            module = _load_python_module(tool_file, tool_file.stem)
            if module and hasattr(module, "run"):
                self._tools[tool_file.stem] = module.run
                self._tools_schema.append(self._generate_tool_schema(tool_file.stem, module.run))
                logger.debug(f"Tool loaded | name={tool_file.stem}")

        logger.info(f"Tools loaded | count={len(self._tools)} | names={list(self._tools.keys())}")

    def _generate_tool_schema(self, name: str, func: Callable[..., Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        """Generate OpenAI-compatible tool schema from function signature."""
        sig = inspect.signature(func)
        doc = inspect.getdoc(func) or ""

        properties: dict[str, Any] = {}
        required: list[str] = []

        for param_name, param in sig.parameters.items():
            if param_name in ("params", "workspace_path"):
                continue

            param_type = "string"
            if param.annotation != inspect.Parameter.empty:
                if param.annotation in (int, float):
                    param_type = "number"
                elif param.annotation is bool:
                    param_type = "boolean"
                elif param.annotation is list:
                    param_type = "array"
                elif param.annotation is dict:
                    param_type = "object"

            properties[param_name] = {"type": param_type}
            if param.default == inspect.Parameter.empty:
                required.append(param_name)

        return {
            "type": "function",
            "function": {
                "name": name,
                "description": doc.split("\n")[0] if doc else name,
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
        }

    def load_skills(self) -> None:
        """Load skills index from workspace/skills/ directory."""
        skills_dir = self._workspace_path / "skills"
        if not skills_dir.exists():
            logger.warning(f"Skills directory not found | path={skills_dir}")
            return

        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            content = skill_md.read_text()
            metadata = self._parse_frontmatter(content)
            name = metadata.get("name") or skill_dir.name
            description = metadata.get("description") or ""
            self._skills_index.append({"name": name, "description": description})
            logger.debug(f"Skill loaded | name={name}")

        logger.info(f"Skills loaded | count={len(self._skills_index)}")

    def _parse_frontmatter(self, content: str) -> dict[str, str]:
        """Parse YAML-like frontmatter from content."""
        if not content.startswith("---"):
            return {}
        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}
        frontmatter = parts[1].strip()
        metadata: dict[str, str] = {}
        for line in frontmatter.split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip()
        return metadata

    async def build_system_prompt(self) -> str:
        """Build system prompt using workspace/systems/builder.py."""
        builder = self._load_builder_module()
        if builder and hasattr(builder, "build_system_prompt"):
            context = {
                "workspace_path": str(self._workspace_path),
                "skills_index": self._skills_index,
                "current_time": datetime.now().isoformat(),
                "history": self._messages,
            }
            prompt = await builder.build_system_prompt(context)
            logger.debug(f"System prompt built by builder.py | length={len(prompt)}")
            return prompt

        # Default implementation
        parts: list[str] = []
        agent_md = self._workspace_path / "AGENT.md"
        if agent_md.exists():
            parts.append(agent_md.read_text())

        if self._skills_index:
            parts.append("\nAvailable skills:")
            for skill in self._skills_index:
                parts.append(f"- {skill['name']}: {skill['description']}")

        prompt = "\n".join(parts)
        logger.debug(f"System prompt built (default) | length={len(prompt)}")
        return prompt

    async def trim_history(self) -> list[dict[str, Any]]:
        """Trim history if it exceeds limit."""
        builder = self._load_builder_module()
        if builder and hasattr(builder, "trim_history"):
            trimmed = await builder.trim_history(self._messages, 100000)
            if len(trimmed) != len(self._messages):
                logger.info(f"History trimmed | original={len(self._messages)} | trimmed={len(trimmed)}")
            return trimmed
        return self._messages

    async def call_llm(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Call LLM Caller via Unix socket."""
        filtered_messages = self._filter_messages(messages)
        logger.debug(f"Calling LLM | message_count={len(filtered_messages)}")

        reader, writer = await asyncio.open_unix_connection(self._config.ai_socket)
        logger.debug("Connected to LLM socket")

        request = LLMRequest(
            id=f"req-{len(self._messages)}",
            messages=filtered_messages,
            tools=self._tools_schema,
            tool_choice="auto",
            stream=True,
        )

        writer.write((request.model_dump_json() + "\n").encode())
        await writer.drain()

        result = await self._read_stream_response(reader, writer)
        logger.debug(f"LLM response received | has_tool_calls={result.get('tool_calls') is not None}")
        return result

    def _filter_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter out invalid tool_calls from messages."""
        filtered: list[dict[str, Any]] = []
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                valid_calls = [
                    tc for tc in msg["tool_calls"] if _is_valid_tool_call_name(tc.get("function", {}).get("name"))
                ]
                if valid_calls:
                    filtered.append({**msg, "tool_calls": valid_calls})
                elif msg.get("content"):
                    filtered.append({"role": "assistant", "content": msg["content"]})
            else:
                filtered.append(msg)
        return filtered

    async def _read_stream_response(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> dict[str, Any]:
        """Read streaming response and return final message."""
        final_message: dict[str, Any] = {"role": "assistant", "content": ""}
        tool_calls: list[dict[str, Any]] = []

        while True:
            data = await reader.readline()
            if not data:
                break
            response = json.loads(data.decode())
            if response.get("done"):
                break

            delta = response["choices"][0]["delta"]

            content = delta.get("content")
            if content:
                final_message["content"] += content

            tc_list = delta.get("tool_calls")
            if tc_list:
                for tc_delta in tc_list:
                    idx = tc_delta.get("index", 0)
                    while idx >= len(tool_calls):
                        tool_calls.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    if tc_delta.get("id"):
                        tool_calls[idx]["id"] = tc_delta["id"]
                    func = tc_delta.get("function")
                    if func:
                        name = func.get("name")
                        if name and name != "null":
                            tool_calls[idx]["function"]["name"] = name
                        if func.get("arguments"):
                            tool_calls[idx]["function"]["arguments"] += func["arguments"]

        writer.close()
        await writer.wait_closed()

        valid_calls = [tc for tc in tool_calls if _is_valid_tool_call_name(tc.get("function", {}).get("name"))]
        if valid_calls:
            final_message["tool_calls"] = valid_calls
            logger.debug(f"Tool calls parsed | count={len(valid_calls)}")

        return final_message

    async def execute_tool(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool call."""
        tool_name = tool_call["function"]["name"]
        params = json.loads(tool_call["function"].get("arguments", "{}"))
        logger.info(f"Executing tool | name={tool_name} | params={params}")

        if tool_name not in self._tools:
            logger.error(f"Tool not found | name={tool_name}")
            return ToolResult(success=False, error=f"Tool '{tool_name}' not found").model_dump()

        result = await self._tools[tool_name](params, str(self._workspace_path))
        logger.debug(f"Tool result | success={result.get('success')}")
        return result

    async def run_react_loop(self, user_message: dict[str, Any]) -> str:
        """Run ReAct loop for a user message."""
        user_content = user_message.get("content", "")
        logger.info(f"ReAct loop started | user_message={user_content[:50]}...")

        self._messages.append(user_message)
        await self.save_message(user_message)

        for iteration in range(1, self._config.max_iterations + 1):
            logger.debug(f"ReAct iteration | iteration={iteration}/{self._config.max_iterations}")

            system_prompt = await self.build_system_prompt()
            messages = await self.trim_history()
            llm_messages = [{"role": "system", "content": system_prompt}] + messages

            response = await self.call_llm(llm_messages)
            content = response.get("content", "")

            assistant_message: dict[str, Any] = {"role": "assistant"}
            if content:
                assistant_message["content"] = content
            if response.get("tool_calls"):
                assistant_message["tool_calls"] = response["tool_calls"]

            self._messages.append(assistant_message)
            await self.save_message(assistant_message)

            tool_calls = response.get("tool_calls")
            if not tool_calls:
                logger.info(f"ReAct loop complete | iterations={iteration}")
                return content

            logger.debug(f"Tool calls detected | count={len(tool_calls)}")
            for tool_call in tool_calls:
                result = await self.execute_tool(tool_call)
                tool_message = {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": json.dumps(result),
                }
                self._messages.append(tool_message)
                await self.save_message(tool_message)

        logger.warning(f"ReAct loop exceeded max iterations | max={self._config.max_iterations}")
        return "Error: Maximum iterations exceeded"

    async def handle_channel(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Handle channel connection."""
        logger.info("Channel connected")

        while True:
            data = await reader.readline()
            if not data:
                break

            user_message = json.loads(data.decode())
            if user_message.get("role") == "user":
                response_text = await self.run_react_loop(user_message)
                response = {"role": "assistant", "content": response_text}
                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()

        writer.close()
        await writer.wait_closed()
        logger.info("Channel disconnected")

    async def run(self) -> None:
        """Start the session server."""
        await self.init_db()
        await self.load_history()
        self.load_tools()
        self.load_skills()

        socket_path = Path(self._config.channel_socket)
        if socket_path.exists():
            socket_path.unlink()

        server = await asyncio.start_unix_server(self.handle_channel, path=self._config.channel_socket)
        logger.info(f"Session server started | socket={self._config.channel_socket}")

        async with server:
            await server.serve_forever()


def _setup_logger(log_level: str) -> None:
    """Configure logger."""
    logger.remove()
    logger.add(
        lambda msg: print(msg, end=""),
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>session</cyan> | {message}",
        level=log_level,
    )


async def run_session(
    workspace_path: str,
    channel_socket: str,
    ai_socket: str,
    session_id: str | None = None,
    log_level: str = "INFO",
) -> None:
    """Python function interface to run a session.

    Args:
        workspace_path: Path to workspace directory
        channel_socket: Unix socket path for channel connections
        ai_socket: Unix socket path for AI caller
        session_id: Optional session identifier
        log_level: Log level (DEBUG, INFO, WARNING, ERROR)
    """
    _setup_logger(log_level)
    config = SessionConfig(
        workspace_path=workspace_path,
        channel_socket=channel_socket,
        ai_socket=ai_socket,
        session_id=session_id or "default",
    )
    session = Session(config)
    await session.run()


@dataclass
class CliArgs:
    """Session CLI arguments."""

    workspace: str
    """Workspace directory path"""

    channel_socket: str
    """Unix socket for channel connections"""

    ai_socket: str
    """Unix socket for AI caller"""

    session_id: str | None = None
    """Session ID (for multi-session support)"""

    log_level: str = "INFO"
    """Log level (DEBUG, INFO, WARNING, ERROR)"""


def main() -> None:
    args = tyro.cli(CliArgs)
    asyncio.run(
        run_session(
            workspace_path=args.workspace,
            channel_socket=args.channel_socket,
            ai_socket=args.ai_socket,
            session_id=args.session_id,
            log_level=args.log_level,
        )
    )


if __name__ == "__main__":
    main()
