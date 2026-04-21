"""Psi Channel - TUI interface for user interaction."""

import asyncio
import json
import sys
from dataclasses import dataclass

import tyro
from loguru import logger
from prompt_toolkit import PromptSession
from prompt_toolkit.styles import Style

from psi_agent.common import AssistantMessage, UserMessage


class Channel:
    """Terminal UI channel for user interaction."""

    def __init__(self, session_socket: str) -> None:
        self._session_socket = session_socket
        logger.debug(f"Channel initialized | socket={session_socket}")

    async def run(self) -> None:
        """Run the TUI interface."""
        logger.info(f"Connecting to session at {self._session_socket}")
        print(f"Connecting to session at {self._session_socket}", file=sys.stderr)

        reader, writer = await asyncio.open_unix_connection(self._session_socket)
        logger.info("Connected to session")

        style = Style.from_dict(
            {
                "user": "#ansigreen",
                "assistant": "#ansicyan",
            }
        )

        prompt_session: PromptSession[str] = PromptSession(style=style)

        print("Connected. Ctrl+C to exit.")

        while True:
            try:
                user_input = await prompt_session.prompt_async("You: ", style=style)
                logger.debug(f"User input received | length={len(user_input)}")

                if not user_input:
                    continue

                message = UserMessage(content=user_input)
                writer.write((message.model_dump_json() + "\n").encode())
                await writer.drain()
                logger.debug(f"Message sent to session | content={user_input[:50]}")

                data = await reader.readline()
                if not data:
                    logger.warning("Session disconnected")
                    print("Session disconnected.")
                    break

                response = AssistantMessage.model_validate(json.loads(data.decode()))
                logger.debug(f"Response received | length={len(response.content)}")
                print(f"Assistant: {response.content}")

            except KeyboardInterrupt:
                logger.info("User interrupted with Ctrl+C")
                print("\nExiting...")
                break
            except EOFError:
                logger.info("EOF received")
                break

        writer.close()
        await writer.wait_closed()
        logger.info("Channel closed")


async def run_channel(session_socket: str, log_level: str = "WARNING") -> None:
    """Python function interface to run the TUI channel.

    Args:
        session_socket: Unix socket path for session connection
        log_level: Log level (DEBUG, INFO, WARNING, ERROR). Default WARNING to not show logs in TUI.
    """
    _setup_logger(log_level)
    channel = Channel(session_socket=session_socket)
    await channel.run()


def _setup_logger(log_level: str) -> None:
    """Configure logger."""
    logger.remove()
    logger.add(
        lambda msg: print(msg, end=""),
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>channel-tui</cyan> | {message}",
        level=log_level,
    )


@dataclass
class CliArgs:
    """Channel TUI CLI arguments."""

    session_socket: str
    """Unix socket path for session connection"""

    log_level: str = "WARNING"
    """Log level (DEBUG, INFO, WARNING, ERROR). Default WARNING to not show logs in TUI"""


def main() -> None:
    args = tyro.cli(CliArgs)
    asyncio.run(run_channel(session_socket=args.session_socket, log_level=args.log_level))


if __name__ == "__main__":
    main()
