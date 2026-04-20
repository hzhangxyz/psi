"""Psi Session - Core ReAct loop engine."""

import argparse
import asyncio
import importlib.util
import inspect
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import aiosqlite
from loguru import logger


class Session:
    """Core session that runs the ReAct loop."""

    def __init__(
        self,
        workspace_path: str,
        channel_socket: str,
        llm_socket: str,
        session_id: str | None = None,
        db_path: str | None = None,
    ) -> None:
        self.workspace_path = Path(workspace_path).resolve()
        self.channel_socket = channel_socket
        self.llm_socket = llm_socket
        self.session_id = session_id or "default"
        self.db_path = db_path or str(self.workspace_path / "state" / f"session-{self.session_id}.db")
        self.messages: list[dict[str, Any]] = []
        self.tools: dict[str, Callable] = {}
        self.tools_schema: list[dict[str, Any]] = []
        self.skills_index: list[dict[str, str]] = []
        self.max_iterations = 10

        logger.info(f"Session initialized | id={self.session_id} | workspace={self.workspace_path}")
        logger.debug(f"Session config | channel_socket={self.channel_socket} | llm_socket={self.llm_socket} | db={self.db_path}")

    async def init_db(self) -> None:
        """Initialize SQLite database for message history."""
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        logger.debug(f"Creating database directory | path={db_dir}")

        async with aiosqlite.connect(self.db_path) as db:
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

        logger.info(f"Database initialized | path={self.db_path}")

    async def load_history(self) -> None:
        """Load message history from database."""
        async with aiosqlite.connect(self.db_path) as db:
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
            self.messages.append(msg)

        logger.info(f"History loaded | count={len(self.messages)} messages")

    async def save_message(self, message: dict[str, Any]) -> None:
        """Save a message to database."""
        async with aiosqlite.connect(self.db_path) as db:
            tool_calls_json = json.dumps(message.get("tool_calls")) if message.get("tool_calls") else None
            await db.execute("""
                INSERT INTO messages (role, content, tool_calls, tool_call_id)
                VALUES (?, ?, ?, ?)
            """, (
                message["role"],
                message.get("content"),
                tool_calls_json,
                message.get("tool_call_id"),
            ))
            await db.commit()

        logger.debug(f"Message saved | role={message['role']} | has_tool_calls={message.get('tool_calls') is not None}")

    def load_tools(self) -> None:
        """Load tools from workspace/tools/ directory."""
        tools_dir = self.workspace_path / "tools"
        if not tools_dir.exists():
            logger.warning(f"Tools directory not found | path={tools_dir}")
            return

        for tool_file in tools_dir.glob("*.py"):
            spec = importlib.util.spec_from_file_location(tool_file.stem, tool_file)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if hasattr(module, "run"):
                    self.tools[tool_file.stem] = module.run
                    self.tools_schema.append(self._generate_tool_schema(tool_file.stem, module.run))
                    logger.debug(f"Tool loaded | name={tool_file.stem}")

        logger.info(f"Tools loaded | count={len(self.tools)} | names={list(self.tools.keys())}")

    def _generate_tool_schema(self, name: str, func: Callable) -> dict[str, Any]:
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
                elif param.annotation == bool:
                    param_type = "boolean"
                elif param.annotation == list:
                    param_type = "array"
                elif param.annotation == dict:
                    param_type = "object"

            properties[param_name] = {"type": param_type}

            if param.default == inspect.Parameter.empty:
                required.append(param_name)

        schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": doc.split("\n")[0] if doc else name,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

        logger.debug(f"Tool schema generated | name={name} | required={required}")
        return schema

    def load_skills(self) -> None:
        """Load skills index from workspace/skills/ directory."""
        skills_dir = self.workspace_path / "skills"
        if not skills_dir.exists():
            logger.warning(f"Skills directory not found | path={skills_dir}")
            return

        for skill_dir in skills_dir.iterdir():
            if skill_dir.is_dir():
                skill_md = skill_dir / "SKILL.md"
                if skill_md.exists():
                    content = skill_md.read_text()
                    if content.startswith("---"):
                        parts = content.split("---")
                        if len(parts) >= 3:
                            frontmatter = parts[1]
                            metadata = {}
                            for line in frontmatter.strip().split("\n"):
                                if ":" in line:
                                    key, value = line.split(":", 1)
                                    metadata[key.strip()] = value.strip()
                            self.skills_index.append({
                                "name": metadata.get("name", skill_dir.name),
                                "description": metadata.get("description", ""),
                            })
                            logger.debug(f"Skill loaded | name={metadata.get('name', skill_dir.name)}")

        logger.info(f"Skills loaded | count={len(self.skills_index)}")

    async def build_system_prompt(self) -> str:
        """Build system prompt using workspace/systems/builder.py."""
        builder_path = self.workspace_path / "systems" / "builder.py"

        if builder_path.exists():
            spec = importlib.util.spec_from_file_location("builder", builder_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if hasattr(module, "build_system_prompt"):
                    context = {
                        "workspace_path": str(self.workspace_path),
                        "skills_index": self.skills_index,
                        "current_time": datetime.now().isoformat(),
                        "history": self.messages,
                    }
                    prompt = await module.build_system_prompt(context)
                    logger.debug(f"System prompt built by builder.py | length={len(prompt)}")
                    return prompt

        # Default implementation
        parts = []
        agent_md = self.workspace_path / "AGENT.md"
        if agent_md.exists():
            parts.append(agent_md.read_text())

        if self.skills_index:
            parts.append("\nAvailable skills:")
            for skill in self.skills_index:
                parts.append(f"- {skill['name']}: {skill['description']}")

        prompt = "\n".join(parts)
        logger.debug(f"System prompt built (default) | length={len(prompt)}")
        return prompt

    async def trim_history(self, limit: int = 100000) -> list[dict[str, Any]]:
        """Trim history if it exceeds token limit."""
        builder_path = self.workspace_path / "systems" / "builder.py"

        original_count = len(self.messages)

        if builder_path.exists():
            spec = importlib.util.spec_from_file_location("builder", builder_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if hasattr(module, "trim_history"):
                    trimmed = await module.trim_history(self.messages, limit)
                    if len(trimmed) != original_count:
                        logger.info(f"History trimmed | original={original_count} | trimmed={len(trimmed)}")
                    return trimmed

        logger.debug(f"History not trimmed | count={original_count}")
        return self.messages

    async def call_llm(self, messages: list[dict[str, Any]], stream: bool = True) -> dict[str, Any]:
        """Call LLM Caller via Unix socket."""
        # Filter out invalid tool_calls from history
        filtered_messages = self._filter_messages(messages)

        logger.debug(f"Calling LLM | stream={stream} | message_count={len(filtered_messages)}")

        try:
            reader, writer = await asyncio.open_unix_connection(self.llm_socket)
            logger.debug(f"Connected to LLM socket | path={self.llm_socket}")

            request = {
                "id": f"req-{len(self.messages)}",
                "messages": filtered_messages,
                "tools": self.tools_schema,
                "tool_choice": "auto",
                "stream": stream,
            }

            writer.write((json.dumps(request) + "\n").encode())
            await writer.drain()
            logger.debug(f"Request sent | id={request['id']}")

            if stream:
                result = await self._read_stream_response(reader, writer)
            else:
                data = await reader.readline()
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
                result = json.loads(data.decode())

            logger.debug(f"LLM response received | has_tool_calls={result.get('tool_calls') is not None}")
            return result

        except Exception as e:
            logger.error(f"LLM connection error | error={e}")
            return {"role": "assistant", "content": f"Connection error: {e}"}

    def _filter_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter out invalid tool_calls from messages."""
        filtered = []
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                # Check if tool_calls have valid names
                valid_tool_calls = []
                for tc in msg["tool_calls"]:
                    func = tc.get("function", {})
                    name = func.get("name")
                    if name and name != "None" and not name.startswith("\x00"):
                        valid_tool_calls.append(tc)
                    else:
                        logger.warning(f"Filtered invalid tool_call | name={name}")
                if valid_tool_calls:
                    msg_copy = dict(msg)
                    msg_copy["tool_calls"] = valid_tool_calls
                    filtered.append(msg_copy)
                else:
                    # No valid tool_calls, just keep content if any
                    if msg.get("content"):
                        filtered.append({"role": "assistant", "content": msg["content"]})
            elif msg.get("role") == "tool":
                # Keep tool messages only if previous assistant had valid tool_calls
                # This is handled by checking the previous message
                filtered.append(msg)
            else:
                filtered.append(msg)
        return filtered

    async def _read_stream_response(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> dict[str, Any]:
        """Read streaming response and return final message."""
        final_message: dict[str, Any] = {"role": "assistant", "content": ""}
        tool_calls: list[dict[str, Any]] = []
        chunk_count = 0

        try:
            while True:
                data = await reader.readline()
                if not data:
                    break

                response = json.loads(data.decode())
                if response.get("done"):
                    break

                chunk_count += 1
                choices = response.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    if not delta:
                        continue
                    content = delta.get("content")
                    if content:
                        final_message["content"] += content
                    tc_list = delta.get("tool_calls")
                    if tc_list:
                        logger.debug(f"Received tool_calls delta | delta={tc_list}")
                        for tc_delta in tc_list:
                            idx = tc_delta.get("index", 0)
                            while idx >= len(tool_calls):
                                tool_calls.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                            if "id" in tc_delta and tc_delta["id"]:
                                tool_calls[idx]["id"] = tc_delta["id"]
                            if "function" in tc_delta:
                                func = tc_delta["function"]
                                if func is None:
                                    continue
                                name = func.get("name")
                                if name and name != "null":
                                    tool_calls[idx]["function"]["name"] = name
                                args = func.get("arguments")
                                if args:
                                    tool_calls[idx]["function"]["arguments"] += args

            logger.debug(f"Stream complete | chunks={chunk_count} | content_length={len(final_message['content'])}")

        except Exception as e:
            logger.error(f"Stream error | error={e}")
            final_message["content"] = f"Stream error: {e}"

        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

        if tool_calls:
            # Filter out invalid tool_calls (empty name)
            valid_tool_calls = []
            for tc in tool_calls:
                name = tc.get("function", {}).get("name", "")
                if name and name != "null" and name != "None":
                    valid_tool_calls.append(tc)
                else:
                    logger.warning(f"Filtered invalid tool_call from response | name={name}")
            if valid_tool_calls:
                final_message["tool_calls"] = valid_tool_calls
                logger.debug(f"Tool calls parsed | count={len(valid_tool_calls)}")

        return final_message

    async def execute_tool(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool call."""
        tool_name = tool_call["function"]["name"]
        try:
            params = json.loads(tool_call["function"]["arguments"])
        except json.JSONDecodeError:
            params = {}

        logger.info(f"Executing tool | name={tool_name} | params={params}")

        if tool_name not in self.tools:
            logger.error(f"Tool not found | name={tool_name}")
            return {"success": False, "error": f"Tool '{tool_name}' not found"}

        try:
            result = await self.tools[tool_name](params, str(self.workspace_path))
            logger.debug(f"Tool result | success={result.get('success')} | has_content={result.get('content') is not None}")
            return result
        except Exception as e:
            logger.error(f"Tool execution error | name={tool_name} | error={e}")
            return {"success": False, "error": str(e)}

    async def run_react_loop(self, user_message: dict[str, Any]) -> str:
        """Run ReAct loop for a user message."""
        user_content = user_message.get("content", "")
        logger.info(f"ReAct loop started | user_message={user_content[:50]}...")

        self.messages.append(user_message)
        await self.save_message(user_message)

        iterations = 0
        while iterations < self.max_iterations:
            iterations += 1
            logger.debug(f"ReAct iteration | iteration={iterations}/{self.max_iterations}")

            # Build system prompt and trim history
            system_prompt = await self.build_system_prompt()
            trimmed_messages = await self.trim_history()

            # Prepare messages for LLM
            llm_messages = [{"role": "system", "content": system_prompt}] + trimmed_messages

            # Call LLM
            response = await self.call_llm(llm_messages)
            if not response:
                logger.error("No response from LLM")
                return "Error: No response from LLM"

            # Check for error response
            content = response.get("content", "")
            if content.startswith("Connection error:") or content.startswith("Stream error:"):
                logger.error(f"LLM error response | error={content}")
                return content

            assistant_message: dict[str, Any] = {"role": "assistant"}
            if content:
                assistant_message["content"] = content
            if response.get("tool_calls"):
                assistant_message["tool_calls"] = response["tool_calls"]

            self.messages.append(assistant_message)
            await self.save_message(assistant_message)

            # Check for tool calls
            tool_calls = response.get("tool_calls")
            if tool_calls:
                logger.debug(f"Tool calls detected | count={len(tool_calls)}")
                for tool_call in tool_calls:
                    result = await self.execute_tool(tool_call)
                    tool_message: dict[str, Any] = {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(result),
                    }
                    self.messages.append(tool_message)
                    await self.save_message(tool_message)
                # Continue loop
            else:
                # No tool calls, return to user
                logger.info(f"ReAct loop complete | iterations={iterations} | response_length={len(content)}")
                return content

        logger.warning(f"ReAct loop exceeded max iterations | max={self.max_iterations}")
        return "Error: Maximum iterations exceeded"

    async def handle_channel(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Handle channel connection."""
        logger.info("Channel connected")

        while True:
            data = await reader.readline()
            if not data:
                break

            try:
                user_message = json.loads(data.decode())
                if user_message.get("role") == "user":
                    # Run ReAct loop
                    response_text = await self.run_react_loop(user_message)

                    # Send response to channel
                    response = {"role": "assistant", "content": response_text}
                    writer.write((json.dumps(response) + "\n").encode())
                    await writer.drain()
                    logger.debug(f"Response sent to channel | length={len(response_text)}")
            except Exception as e:
                logger.error(f"Channel handling error | error={e}")
                error_response = {"role": "assistant", "content": f"Error: {e}"}
                writer.write((json.dumps(error_response) + "\n").encode())
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

        # Remove existing socket
        socket_path = Path(self.channel_socket)
        if socket_path.exists():
            socket_path.unlink()
            logger.debug(f"Removed existing socket | path={self.channel_socket}")

        server = await asyncio.start_unix_server(
            self.handle_channel,
            path=self.channel_socket,
        )

        logger.info(f"Session server started | socket={self.channel_socket} | session_id={self.session_id}")

        async with server:
            await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Psi Session")
    parser.add_argument("--workspace", required=True, help="Workspace directory path")
    parser.add_argument("--channel-socket", required=True, help="Unix socket for channel")
    parser.add_argument("--llm-socket", required=True, help="Unix socket for LLM caller")
    parser.add_argument("--session-id", default=None, help="Session ID (for multi-session)")
    parser.add_argument("--log-level", default="INFO", help="Log level (DEBUG, INFO, WARNING, ERROR)")
    args = parser.parse_args()

    # Configure logger
    logger.remove()
    logger.add(
        lambda msg: print(msg, end=""),
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>session</cyan> | {message}",
        level=args.log_level,
    )

    session = Session(
        workspace_path=args.workspace,
        channel_socket=args.channel_socket,
        llm_socket=args.llm_socket,
        session_id=args.session_id,
    )

    asyncio.run(session.run())


if __name__ == "__main__":
    main()