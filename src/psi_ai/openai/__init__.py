"""Psi AI OpenAI - LLM Caller that exposes OpenAI-compatible API via Unix socket."""

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tyro
from loguru import logger
from openai import AsyncOpenAI

from psi_common import LLMRequest, LLMResponse


class AICaller:
    """LLM Caller that forwards requests to OpenAI-compatible APIs."""

    session_socket: str
    client: AsyncOpenAI
    model: str

    def __init__(
        self,
        session_socket: str,
        api_key: str,
        base_url: str,
        model: str,
    ) -> None:
        self.session_socket = session_socket
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model

        logger.info(f"AI Caller initialized | model={model} | base_url={base_url}")
        logger.debug(f"AI Caller config | socket={session_socket}")

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Handle a single client connection."""
        request_id: str = "unknown"
        try:
            data = await reader.readline()
            if not data:
                logger.debug("Empty request received")
                return

            request_data = json.loads(data.decode())
            request = LLMRequest.model_validate(request_data)
            request_id = request.id
            messages = request.messages
            tools = request.tools
            tool_choice = request.tool_choice
            stream = request.stream

            logger.info(f"Request received | id={request_id} | stream={stream} | message_count={len(messages)}")
            logger.debug(f"Request details | has_tools={tools is not None} | tool_choice={tool_choice}")

            if stream:
                await self._handle_stream(request_id, messages, tools, tool_choice, writer)
            else:
                await self._handle_non_stream(request_id, messages, tools, tool_choice, writer)

        except Exception as e:
            error_str = str(e)
            # Only handle network-related errors gracefully
            if "Connection" in error_str or "Broken pipe" in error_str or "Pipe" in error_str:
                logger.debug(f"Connection closed by client | error={e}")
                return
            # Non-network errors: let it crash
            logger.error(f"Request handling error | error={e}")
            raise

        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_stream(
        self,
        request_id: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        tool_choice: str,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle streaming request."""
        logger.debug(f"Starting stream | request_id={request_id}")

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        try:
            stream = await self.client.chat.completions.create(**kwargs)
            chunk_count = 0

            async for chunk in stream:
                chunk_count += 1
                chunk_data = chunk.model_dump()
                response = LLMResponse(id=request_id, choices=chunk_data.get("choices", []))
                writer.write((response.model_dump_json() + "\n").encode())
                await writer.drain()

            # Send done marker
            done_response = LLMResponse(id=request_id, choices=[], done=True)
            writer.write((done_response.model_dump_json() + "\n").encode())
            await writer.drain()

            logger.info(f"Stream complete | request_id={request_id} | chunks={chunk_count}")

        except Exception as e:
            logger.error(f"Stream error | request_id={request_id} | error={e}")
            raise

    async def _handle_non_stream(
        self,
        request_id: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        tool_choice: str,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle non-streaming request."""
        logger.debug(f"Starting non-stream request | request_id={request_id}")

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        try:
            response = await self.client.chat.completions.create(**kwargs)
            response_data = response.model_dump()
            result = {"id": request_id, "choices": response_data.get("choices", [])}
            writer.write((json.dumps(result) + "\n").encode())
            await writer.drain()

            content = response_data.get("choices", [{}])[0].get("message", {}).get("content", "")
            logger.info(f"Non-stream complete | request_id={request_id} | response_length={len(content)}")

        except Exception as e:
            logger.error(f"Non-stream error | request_id={request_id} | error={e}")
            raise

    async def run(self) -> None:
        """Start the Unix socket server."""
        socket_path = Path(self.session_socket)
        if socket_path.exists():
            socket_path.unlink()
            logger.debug(f"Removed existing socket | path={self.session_socket}")

        server = await asyncio.start_unix_server(
            self.handle_client,
            path=self.session_socket,
        )

        logger.info(f"AI server started | socket={self.session_socket} | model={self.model}")

        async with server:
            await server.serve_forever()


async def run_ai(
    session_socket: str,
    model: str,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    log_level: str = "INFO",
) -> None:
    """Python function interface to run the AI caller.

    Args:
        session_socket: Unix socket path to listen on
        model: Model name to use
        api_key: API key for the LLM provider
        base_url: API base URL
        log_level: Log level (DEBUG, INFO, WARNING, ERROR)
    """
    _setup_logger(log_level)
    caller = AICaller(
        session_socket=session_socket,
        api_key=api_key,
        base_url=base_url,
        model=model,
    )
    await caller.run()


def _setup_logger(log_level: str) -> None:
    """Configure logger."""
    logger.remove()
    logger.add(
        lambda msg: print(msg, end=""),
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>ai-openai</cyan> | {message}",
        level=log_level,
    )


@dataclass
class CliArgs:
    """AI Caller CLI arguments."""

    session_socket: str
    """Unix socket path to listen on"""

    model: str
    """Model name to use"""

    api_key: str | None = None
    """API key (or set API_KEY env var)"""

    base_url: str = "https://api.openai.com/v1"
    """API base URL"""

    log_level: str = "INFO"
    """Log level (DEBUG, INFO, WARNING, ERROR)"""


def main() -> None:
    args = tyro.cli(CliArgs)

    api_key = args.api_key or os.environ.get("API_KEY")
    if not api_key:
        _setup_logger(args.log_level)
        logger.error("API key required via --api-key or API_KEY env var")
        print("Error: API key required via --api-key or API_KEY env var", file=sys.stderr)
        sys.exit(1)

    asyncio.run(
        run_ai(
            session_socket=args.session_socket,
            model=args.model,
            api_key=api_key,
            base_url=args.base_url,
            log_level=args.log_level,
        )
    )


if __name__ == "__main__":
    main()
