"""Integration tests for Session <-> Channel communication."""

import asyncio
import contextlib
import json
import os
from pathlib import Path

import pytest

from psi_agent.session import Session, SessionConfig

# Skip if API key not set (needed for full integration)
API_KEY = os.environ.get("OPENAI_API_KEY")
pytestmark = pytest.mark.skipif(
    not API_KEY,
    reason="OPENAI_API_KEY environment variable not set",
)


class MockAICaller:
    """Mock AI Caller for testing without real API."""

    def __init__(self, socket_path: str):
        self.socket_path = socket_path
        self.responses = []

    def set_response(self, content: str):
        """Set the response to return."""
        self.responses = [{"role": "assistant", "content": content}]

    async def run(self):
        """Run mock server."""
        if Path(self.socket_path).exists():
            Path(self.socket_path).unlink()

        server = await asyncio.start_unix_server(
            self._handle_client,
            path=self.socket_path,
        )

        async with server:
            await server.serve_forever()

    async def _handle_client(self, reader, writer):
        """Handle client request."""
        data = await reader.readline()
        if not data:
            return

        request = json.loads(data.decode())
        stream = request.get("stream", True)

        if stream:
            # Send streaming response
            for resp in self.responses:
                chunk = {"id": request.get("id", "test"), "choices": [{"delta": resp}]}
                writer.write((json.dumps(chunk) + "\n").encode())
                await writer.drain()

            # Send done marker
            done = {"id": request.get("id", "test"), "done": True}
            writer.write((json.dumps(done) + "\n").encode())
            await writer.drain()
        else:
            # Send non-streaming response
            result = {"id": request.get("id", "test"), "choices": [{"message": self.responses[0]}]}
            writer.write((json.dumps(result) + "\n").encode())
            await writer.drain()

        writer.close()
        await writer.wait_closed()


@pytest.fixture
async def mock_ai_server(tmp_path):
    """Start mock AI Caller server."""
    socket_path = str(tmp_path / "ai.sock")
    mock = MockAICaller(socket_path)
    mock.set_response("Hello from mock AI!")

    server_task = asyncio.create_task(mock.run())
    await asyncio.sleep(0.5)

    yield mock

    server_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await server_task

    if Path(socket_path).exists():
        Path(socket_path).unlink()


@pytest.fixture
async def session_server(tmp_path, mock_ai_server):
    """Start session server."""
    workspace_path = str(tmp_path / "workspace")
    channel_socket = str(tmp_path / "channel.sock")
    ai_socket = mock_ai_server.socket_path

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

    # Start session server
    server_task = asyncio.create_task(sess.run())
    await asyncio.sleep(0.5)

    yield sess

    server_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await server_task

    if Path(channel_socket).exists():
        Path(channel_socket).unlink()


class TestSessionChannelIntegration:
    """Test Session <-> Channel integration."""

    @pytest.mark.asyncio
    async def test_channel_sends_message(self, session_server):
        """Test channel sends message and receives response."""
        reader, writer = await asyncio.open_unix_connection(session_server._config.channel_socket)

        # Send user message
        user_message = {"role": "user", "content": "Hello!"}
        writer.write((json.dumps(user_message) + "\n").encode())
        await writer.drain()

        # Receive response
        data = await reader.readline()
        response = json.loads(data.decode())

        writer.close()
        await writer.wait_closed()

        assert response["role"] == "assistant"
        assert "content" in response
        assert len(response["content"]) > 0

    @pytest.mark.asyncio
    async def test_channel_multiple_messages(self, session_server, mock_ai_server):
        """Test multiple messages in sequence."""
        reader, writer = await asyncio.open_unix_connection(session_server._config.channel_socket)

        # First message
        mock_ai_server.set_response("First response")
        user_message = {"role": "user", "content": "First message"}
        writer.write((json.dumps(user_message) + "\n").encode())
        await writer.drain()

        data = await reader.readline()
        response1 = json.loads(data.decode())
        assert "First response" in response1["content"]

        # Second message
        mock_ai_server.set_response("Second response")
        user_message = {"role": "user", "content": "Second message"}
        writer.write((json.dumps(user_message) + "\n").encode())
        await writer.drain()

        data = await reader.readline()
        response2 = json.loads(data.decode())
        assert "Second response" in response2["content"]

        writer.close()
        await writer.wait_closed()

    @pytest.mark.asyncio
    async def test_session_persists_history(self, session_server, mock_ai_server):
        """Test session saves messages to database."""
        reader, writer = await asyncio.open_unix_connection(session_server._config.channel_socket)

        mock_ai_server.set_response("Test response")
        user_message = {"role": "user", "content": "Test message"}
        writer.write((json.dumps(user_message) + "\n").encode())
        await writer.drain()

        data = await reader.readline()
        _ = json.loads(data.decode())

        writer.close()
        await writer.wait_closed()

        # Check database
        import aiosqlite

        db_path = session_server._db_path
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("SELECT role, content FROM messages ORDER BY id")
            rows = list(await cursor.fetchall())

        # Should have at least user and assistant messages
        assert len(rows) >= 2
        assert rows[0][0] == "user"
        assert rows[1][0] == "assistant"


class TestSessionWithRealAI:
    """Test Session with real AI Caller (requires API key)."""

    @pytest.mark.asyncio
    async def test_full_session_flow(self, tmp_path):
        """Test full session flow with real AI."""
        from psi_agent.ai.openai import AICaller

        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

        # Setup paths
        ai_socket = str(tmp_path / "ai.sock")
        channel_socket = str(tmp_path / "channel.sock")
        workspace_path = str(tmp_path / "workspace")

        # Create workspace
        workspace = Path(workspace_path)
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "AGENT.md").write_text("You are a helpful assistant. Be concise.")

        # Start AI server
        # API_KEY is guaranteed to be set due to pytestmark skipif
        assert API_KEY is not None
        ai_caller = AICaller(
            session_socket=ai_socket,
            api_key=API_KEY,
            base_url=base_url,
            model=model,
        )
        ai_task = asyncio.create_task(ai_caller.run())
        await asyncio.sleep(0.5)

        # Start session
        config = SessionConfig(
            workspace_path=workspace_path,
            channel_socket=channel_socket,
            ai_socket=ai_socket,
        )
        sess = Session(config)
        await sess.init_db()
        sess.load_tools()
        sess.load_skills()
        session_task = asyncio.create_task(sess.run())
        await asyncio.sleep(0.5)

        try:
            # Connect as channel
            reader, writer = await asyncio.open_unix_connection(channel_socket)

            # Send message
            user_message = {"role": "user", "content": "Say 'hello' in one word"}
            writer.write((json.dumps(user_message) + "\n").encode())
            await writer.drain()

            # Receive response
            data = await reader.readline()
            response = json.loads(data.decode())

            writer.close()
            await writer.wait_closed()

            assert response["role"] == "assistant"
            assert len(response["content"]) > 0

        finally:
            # Cleanup
            session_task.cancel()
            ai_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session_task
            with contextlib.suppress(asyncio.CancelledError):
                await ai_task

            for path in [ai_socket, channel_socket]:
                if Path(path).exists():
                    Path(path).unlink()
