"""Bash tool - execute shell commands."""

import subprocess


async def run(params: dict, workspace_path: str) -> dict:
    """Execute a shell command.

    Args:
        command: The shell command to execute.
    """
    command = params.get("command", "")
    if not command:
        return {"success": False, "error": "command parameter required"}

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=workspace_path,
        )
        return {
            "success": True,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}