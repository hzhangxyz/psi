"""Tests for psi_channel.tui."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from psi_channel.tui import Channel, run_channel


class TestChannelInit:
    """Test Channel initialization."""

    def test_channel_sets_socket(self):
        """Test channel sets session_socket."""
        channel = Channel(session_socket="/tmp/test.sock")
        assert channel.session_socket == "/tmp/test.sock"


class TestChannelRun:
    """Test Channel.run method with mocks."""

    @pytest.mark.asyncio
    async def test_channel_connects_and_sends_message(self, capsys):
        """Test channel connects and sends/receives message."""
        channel = Channel(session_socket="/tmp/test.sock")

        mock_reader = asyncio.StreamReader()
        mock_writer = MagicMock()
        mock_writer.write = MagicMock()
        mock_writer.drain = AsyncMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()

        response = {"role": "assistant", "content": "Hello back!"}
        mock_reader.feed_data((json.dumps(response) + "\n").encode())
        mock_reader.feed_eof()

        mock_prompt_session = MagicMock()
        mock_prompt_session.prompt_async = AsyncMock(side_effect=["Hello", KeyboardInterrupt])

        with (
            patch("asyncio.open_unix_connection", return_value=(mock_reader, mock_writer)),
            patch("psi_channel.tui.PromptSession", return_value=mock_prompt_session),
        ):
            await channel.run()

        assert mock_writer.write.called
        sent_data = mock_writer.write.call_args[0][0]
        sent_message = json.loads(sent_data.decode())
        assert sent_message["role"] == "user"
        assert sent_message["content"] == "Hello"

        captured = capsys.readouterr()
        assert "Hello back!" in captured.out

    @pytest.mark.asyncio
    async def test_channel_handles_keyboard_interrupt(self, capsys):
        """Test channel handles Ctrl+C."""
        channel = Channel(session_socket="/tmp/test.sock")

        mock_reader = asyncio.StreamReader()
        mock_writer = MagicMock()
        mock_writer.write = MagicMock()
        mock_writer.drain = AsyncMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()

        mock_prompt_session = MagicMock()
        mock_prompt_session.prompt_async = AsyncMock(side_effect=KeyboardInterrupt)

        with (
            patch("asyncio.open_unix_connection", return_value=(mock_reader, mock_writer)),
            patch("psi_channel.tui.PromptSession", return_value=mock_prompt_session),
        ):
            await channel.run()

        captured = capsys.readouterr()
        assert "Exiting" in captured.out

    @pytest.mark.asyncio
    async def test_channel_handles_eof(self):
        """Test channel handles EOF."""
        channel = Channel(session_socket="/tmp/test.sock")

        mock_reader = asyncio.StreamReader()
        mock_writer = MagicMock()
        mock_writer.write = MagicMock()
        mock_writer.drain = AsyncMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()

        mock_prompt_session = MagicMock()
        mock_prompt_session.prompt_async = AsyncMock(side_effect=EOFError)

        with (
            patch("asyncio.open_unix_connection", return_value=(mock_reader, mock_writer)),
            patch("psi_channel.tui.PromptSession", return_value=mock_prompt_session),
        ):
            await channel.run()

    @pytest.mark.asyncio
    async def test_channel_handles_empty_input(self):
        """Test channel skips empty input."""
        channel = Channel(session_socket="/tmp/test.sock")

        mock_reader = asyncio.StreamReader()
        mock_writer = MagicMock()
        mock_writer.write = MagicMock()
        mock_writer.drain = AsyncMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()

        mock_prompt_session = MagicMock()
        mock_prompt_session.prompt_async = AsyncMock(side_effect=["", "Hello", KeyboardInterrupt])

        response = {"role": "assistant", "content": "Response"}
        mock_reader.feed_data((json.dumps(response) + "\n").encode())

        with (
            patch("asyncio.open_unix_connection", return_value=(mock_reader, mock_writer)),
            patch("psi_channel.tui.PromptSession", return_value=mock_prompt_session),
        ):
            await channel.run()

        assert mock_writer.write.call_count == 1

    @pytest.mark.asyncio
    async def test_channel_handles_session_disconnect(self, capsys):
        """Test channel handles session disconnect."""
        channel = Channel(session_socket="/tmp/test.sock")

        mock_reader = asyncio.StreamReader()
        mock_writer = MagicMock()
        mock_writer.write = MagicMock()
        mock_writer.drain = AsyncMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()

        mock_reader.feed_eof()

        mock_prompt_session = MagicMock()
        mock_prompt_session.prompt_async = AsyncMock(side_effect=["Hello"])

        with (
            patch("asyncio.open_unix_connection", return_value=(mock_reader, mock_writer)),
            patch("psi_channel.tui.PromptSession", return_value=mock_prompt_session),
        ):
            await channel.run()

        captured = capsys.readouterr()
        assert "Session disconnected" in captured.out


class TestRunChannel:
    """Test run_channel function."""

    @pytest.mark.asyncio
    async def test_run_channel_creates_channel(self):
        """Test run_channel creates Channel instance."""
        with patch("psi_channel.tui.Channel") as mock_channel_class:
            mock_channel = AsyncMock()
            mock_channel_class.return_value = mock_channel

            await run_channel(session_socket="/tmp/test.sock", log_level="ERROR")

            mock_channel_class.assert_called_once_with(session_socket="/tmp/test.sock")
            mock_channel.run.assert_called_once()
