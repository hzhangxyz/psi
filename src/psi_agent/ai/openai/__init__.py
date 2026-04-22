"""Psi AI OpenAI - LLM Caller that exposes OpenAI-compatible API via Unix socket."""

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tyro
from loguru import logger
from openai import AsyncOpenAI

from psi_agent.common import LLMRequest, LLMResponse


class AICaller:
    """LLM Caller that forwards requests to OpenAI-compatible APIs."""

    def __init__(
        self,
        session_socket: str,
        api_key: str,
        base_url: str,
        model: str,
    ) -> None:
        self._session_socket = session_socket
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model

        logger.info(f"AI Caller initialized | model={model} | base_url={base_url}")
        logger.debug(f"AI Caller config | socket={session_socket}")

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Handle a single client connection."""
        data = await reader.readline()
        if not data:
            logger.debug("Empty request received")
            return

        request = LLMRequest.model_validate(json.loads(data.decode()))
        logger.info(
            f"Request received | id={request.id} | stream={request.stream} | message_count={len(request.messages)}"
        )
        logger.debug(f"Request details | has_tools={request.tools is not None} | tool_choice={request.tool_choice}")

        if request.stream:
            await self._handle_stream(request, writer)
        else:
            await self._handle_non_stream(request, writer)

        writer.close()
        await writer.wait_closed()

    async def _handle_stream(self, request: LLMRequest, writer: asyncio.StreamWriter) -> None:
        """Handle streaming request."""
        logger.debug(f"Starting stream | request_id={request.id}")

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": request.messages,
            "stream": True,
        }
        if request.tools:
            kwargs["tools"] = request.tools
            kwargs["tool_choice"] = request.tool_choice

        stream = await self._client.chat.completions.create(**kwargs)
        chunk_count = 0

        async for chunk in stream:
            chunk_count += 1
            chunk_data = chunk.model_dump()
            response = LLMResponse(id=request.id, choices=chunk_data.get("choices", []))
            writer.write((response.model_dump_json() + "\n").encode())
            await writer.drain()

        # Send done marker
        done_response = LLMResponse(id=request.id, choices=[], done=True)
        writer.write((done_response.model_dump_json() + "\n").encode())
        await writer.drain()

        logger.info(f"Stream complete | request_id={request.id} | chunks={chunk_count}")

    async def _handle_non_stream(self, request: LLMRequest, writer: asyncio.StreamWriter) -> None:
        """Handle non-streaming request."""
        logger.debug(f"Starting non-stream request | request_id={request.id}")

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": request.messages,
        }
        if request.tools:
            kwargs["tools"] = request.tools
            kwargs["tool_choice"] = request.tool_choice

        response = await self._client.chat.completions.create(**kwargs)
        response_data = response.model_dump()
        llm_response = LLMResponse(id=request.id, choices=response_data.get("choices", []))
        writer.write((llm_response.model_dump_json() + "\n").encode())
        await writer.drain()

        content = response_data.get("choices", [{}])[0].get("message", {}).get("content", "")
        logger.info(f"Non-stream complete | request_id={request.id} | response_length={len(content)}")

    async def run(self) -> None:
        """Start the Unix socket server."""
        socket_path = Path(self._session_socket)
        if socket_path.exists():
            socket_path.unlink()
            logger.debug(f"Removed existing socket | path={self._session_socket}")

        server = await asyncio.start_unix_server(
            self.handle_client,
            path=self._session_socket,
        )

        logger.info(f"AI server started | socket={self._session_socket} | model={self._model}")

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

    api_key = args.api_key or os.environ.get("API_KEY") or ""

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
