"""Integration tests for AI Caller <-> Session communication."""

import asyncio
import contextlib
import json
import os
from pathlib import Path

import pytest

from psi_ai.openai import AICaller
from psi_common import LLMRequest
from psi_session import Session, SessionConfig

# Environment variables for API configuration (OpenAI style)
API_KEY = os.environ.get("OPENAI_API_KEY")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# Skip all tests in this module if API key is not configured
pytestmark = pytest.mark.skipif(
    not API_KEY,
    reason="OPENAI_API_KEY environment variable not set",
)


@pytest.fixture
async def ai_server(tmp_path):
    """Start AI Caller server."""
    socket_path = str(tmp_path / "ai.sock")
    # API_KEY is guaranteed to be set due to pytestmark skipif
    assert API_KEY is not None
    caller = AICaller(
        session_socket=socket_path,
        api_key=API_KEY,
        base_url=BASE_URL,
        model=MODEL,
    )

    # Start server in background
    server_task = asyncio.create_task(caller.run())

    # Wait for socket to be created
    await asyncio.sleep(0.5)

    yield caller

    # Cleanup
    server_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await server_task

    # Remove socket file
    if Path(socket_path).exists():
        Path(socket_path).unlink()


@pytest.fixture
async def session(tmp_path, ai_server):
    """Create a session connected to AI server."""
    workspace_path = str(tmp_path / "workspace")
    channel_socket = str(tmp_path / "channel.sock")
    ai_socket = ai_server.session_socket

    # Create minimal workspace
    workspace = Path(workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "AGENT.md").write_text("You are a helpful assistant.")

    config = SessionConfig(
        workspace_path=workspace_path,
        channel_socket=channel_socket,
        ai_socket=ai_socket,
    )
    sess = Session(config)
    await sess.init_db()
    sess.load_tools()
    sess.load_skills()

    yield sess


class TestAISessionIntegration:
    """Test AI Caller <-> Session integration."""

    @pytest.mark.asyncio
    async def test_session_calls_ai_non_stream(self, session):
        """Test session calls AI with non-streaming request."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say 'hello' in one word."},
        ]

        # Modify session to use non-streaming
        reader, writer = await asyncio.open_unix_connection(session._config.ai_socket)

        request = LLMRequest(
            id="test-1",
            messages=messages,
            stream=False,
        )

        writer.write((request.model_dump_json() + "\n").encode())
        await writer.drain()

        # Read response
        data = await reader.readline()
        response = json.loads(data.decode())

        writer.close()
        await writer.wait_closed()

        assert "id" in response
        assert "choices" in response
        assert len(response["choices"]) > 0
        content = response["choices"][0].get("message", {}).get("content", "")
        assert len(content) > 0

    @pytest.mark.asyncio
    async def test_session_calls_ai_stream(self, session):
        """Test session calls AI with streaming request."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say 'hello' in one word."},
        ]

        result = await session.call_llm(messages)

        assert result["role"] == "assistant"
        assert "content" in result
        assert len(result["content"]) > 0

    @pytest.mark.asyncio
    async def test_session_react_loop_simple(self, session):
        """Test simple ReAct loop without tools."""
        user_message = {"role": "user", "content": "What is 2+2? Reply with just the number."}

        response = await session.run_react_loop(user_message)

        assert len(response) > 0
        # Should contain "4" somewhere
        assert "4" in response or "four" in response.lower()

    @pytest.mark.asyncio
    async def test_session_with_tools(self, tmp_path, ai_server):
        """Test session with tools execution."""
        workspace_path = str(tmp_path / "workspace_tools")
        channel_socket = str(tmp_path / "channel.sock")
        ai_socket = ai_server.session_socket

        # Create workspace with a simple tool
        workspace = Path(workspace_path)
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "AGENT.md").write_text("You are a helpful assistant. Use tools when asked.")

        tools_dir = workspace / "tools"
        tools_dir.mkdir()
        (tools_dir / "echo.py").write_text(
            '''
"""Echo tool - returns the input."""

async def run(params: dict, workspace_path: str) -> dict:
    """Echo back the input message.

    Args:
        message: Message to echo back.
    """
    message = params.get("message", "")
    return {"success": True, "content": f"Echo: {message}"}
'''
        )

        config = SessionConfig(
            workspace_path=workspace_path,
            channel_socket=channel_socket,
            ai_socket=ai_socket,
        )
        sess = Session(config)
        await sess.init_db()
        sess.load_tools()
        sess.load_skills()

        # Ask LLM to use the tool
        user_message = {"role": "user", "content": "Use the echo tool to echo 'test message'"}

        response = await sess.run_react_loop(user_message)

        # Should have executed the tool
        assert len(response) > 0

    @pytest.mark.asyncio
    async def test_ai_error_handling(self, ai_server):
        """Test AI caller raises on invalid JSON (let it crash)."""
        reader, writer = await asyncio.open_unix_connection(ai_server.session_socket)

        # Send invalid JSON
        writer.write(b"invalid json\n")
        await writer.drain()

        # With "let it crash", AI caller raises and closes connection
        # Client receives EOF (empty data) instead of error response
        data = await reader.readline()
        assert len(data) == 0  # Connection closed by server

        writer.close()
        await writer.wait_closed()
