"""Bash tool - execute shell commands."""

import asyncio
from typing import Any


async def run(params: dict[str, Any], workspace_path: str) -> dict[str, Any]:
    """Execute a shell command.

    Args:
        command: The shell command to execute.
    """
    command = params.get("command", "")
    if not command:
        return {"success": False, "error": "command parameter required"}

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace_path,
        )
        stdout, stderr = await proc.communicate()
        return {
            "success": True,
            "stdout": stdout.decode() if stdout else "",
            "stderr": stderr.decode() if stderr else "",
            "returncode": proc.returncode or 0,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
