"""Psi Channel - TUI interface for user interaction."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.styles import Style
    HAS_PROMPT_TOOLKIT = True
except ImportError:
    HAS_PROMPT_TOOLKIT = False


class Channel:
    """Terminal UI channel for user interaction."""

    def __init__(self, session_socket: str) -> None:
        self.session_socket = session_socket

    async def run_simple(self) -> None:
        """Simple readline-based interface."""
        print(f"Connecting to session at {self.session_socket}", file=sys.stderr)

        reader, writer = await asyncio.open_unix_connection(self.session_socket)

        print("Connected. Type your message and press Enter.")

        while True:
            try:
                user_input = input("You: ")
                if not user_input:
                    continue

                # Send to session
                message = {"role": "user", "content": user_input}
                writer.write((json.dumps(message) + "\n").encode())
                await writer.drain()

                # Read response
                data = await reader.readline()
                if not data:
                    print("Session disconnected.")
                    break

                response = json.loads(data.decode())
                print(f"Assistant: {response.get('content', '')}")

            except EOFError:
                break
            except KeyboardInterrupt:
                print("\nExiting...")
                break

        writer.close()
        await writer.wait_closed()

    async def run_prompt_toolkit(self) -> None:
        """prompt_toolkit-based interface with colors."""
        print(f"Connecting to session at {self.session_socket}", file=sys.stderr)

        reader, writer = await asyncio.open_unix_connection(self.session_socket)

        style = Style.from_dict({
            "user": "#ansigreen",
            "assistant": "#ansicyan",
        })

        session = PromptSession(style=style)

        print("Connected. Ctrl+C to exit.")

        while True:
            try:
                user_input = await session.prompt_async("You: ", style=style)

                if not user_input:
                    continue

                message = {"role": "user", "content": user_input}
                writer.write((json.dumps(message) + "\n").encode())
                await writer.drain()

                data = await reader.readline()
                if not data:
                    print("Session disconnected.")
                    break

                response = json.loads(data.decode())
                print(f"Assistant: {response.get('content', '')}")

            except KeyboardInterrupt:
                print("\nExiting...")
                break
            except EOFError:
                break

        writer.close()
        await writer.wait_closed()

    async def run(self) -> None:
        """Run the channel interface."""
        if HAS_PROMPT_TOOLKIT:
            await self.run_prompt_toolkit()
        else:
            await self.run_simple()


def main() -> None:
    parser = argparse.ArgumentParser(description="Psi Channel - TUI interface")
    parser.add_argument("--session-socket", required=True, help="Unix socket path for session")
    args = parser.parse_args()

    channel = Channel(session_socket=args.session_socket)
    asyncio.run(channel.run())


if __name__ == "__main__":
    main()