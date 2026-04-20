"""Read file tool."""

async def run(params: dict, workspace_path: str) -> dict:
    """Read a file from the workspace.

    Args:
        path: Relative path to the file in workspace.
    """
    from pathlib import Path

    path = params.get("path", "")
    if not path:
        return {"success": False, "error": "path parameter required"}

    file_path = Path(workspace_path) / path

    if not file_path.exists():
        return {"success": False, "error": f"File not found: {path}"}

    try:
        content = file_path.read_text()
        return {"success": True, "content": content}
    except Exception as e:
        return {"success": False, "error": str(e)}