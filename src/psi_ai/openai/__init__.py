"""Psi AI OpenAI - LLM Caller that exposes OpenAI-compatible API via Unix socket."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from loguru import logger
from openai import AsyncOpenAI


class AICaller:
    """LLM Caller that forwards requests to OpenAI-compatible APIs."""

    def __init__(
        self,
        socket_path: str,
        api_key: str,
        base_url: str,
        model: str,
    ) -> None:
        self.socket_path = socket_path
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model

        logger.info(f"AI Caller initialized | model={model} | base_url={base_url}")
        logger.debug(f"AI Caller config | socket={socket_path}")

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Handle a single client connection."""
        try:
            data = await reader.readline()
            if not data:
                logger.debug("Empty request received")
                return

            request = json.loads(data.decode())
            request_id = request.get("id", "unknown")
            messages = request.get("messages", [])
            tools = request.get("tools")
            tool_choice = request.get("tool_choice", "auto")
            stream = request.get("stream", True)

            logger.info(f"Request received | id={request_id} | stream={stream} | message_count={len(messages)}")
            logger.debug(f"Request details | has_tools={tools is not None} | tool_choice={tool_choice}")

            if stream:
                await self._handle_stream(request_id, messages, tools, tool_choice, writer)
            else:
                await self._handle_non_stream(request_id, messages, tools, tool_choice, writer)

        except Exception as e:
            error_str = str(e)
            if "Connection" in error_str or "Broken pipe" in error_str or "Pipe" in error_str:
                logger.debug(f"Connection closed by client | error={e}")
                return
            logger.error(f"Request handling error | error={e}")
            error_response = {"id": request.get("id", "error"), "error": error_str}
            writer.write((json.dumps(error_response) + "\n").encode())
            await writer.drain()

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
                response = {"id": request_id, "choices": chunk_data.get("choices", [])}
                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()

            # Send done marker
            writer.write((json.dumps({"id": request_id, "done": True}) + "\n").encode())
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
        # Remove existing socket
        socket_path = Path(self.socket_path)
        if socket_path.exists():
            socket_path.unlink()
            logger.debug(f"Removed existing socket | path={self.socket_path}")

        server = await asyncio.start_unix_server(
            self.handle_client,
            path=self.socket_path,
        )

        logger.info(f"AI server started | socket={self.socket_path} | model={self.model}")

        async with server:
            await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Psi AI OpenAI - LLM Caller")
    parser.add_argument("--socket", required=True, help="Unix socket path")
    parser.add_argument("--provider", default="openai", help="LLM provider (for logging)")
    parser.add_argument("--model", required=True, help="Model name")
    parser.add_argument("--api-key", help="API key (or set API_KEY env var)")
    parser.add_argument("--base-url", default="https://api.openai.com/v1", help="API base URL")
    parser.add_argument("--log-level", default="INFO", help="Log level (DEBUG, INFO, WARNING, ERROR)")
    args = parser.parse_args()

    # Configure logger
    logger.remove()
    logger.add(
        lambda msg: print(msg, end=""),
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>ai-openai</cyan> | {message}",
        level=args.log_level,
    )

    api_key = args.api_key or os.environ.get("API_KEY")
    if not api_key:
        logger.error("API key required via --api-key or API_KEY env var")
        print("Error: API key required via --api-key or API_KEY env var", file=sys.stderr)
        sys.exit(1)

    caller = AICaller(
        socket_path=args.socket,
        api_key=api_key,
        base_url=args.base_url,
        model=args.model,
    )

    asyncio.run(caller.run())


if __name__ == "__main__":
    main()