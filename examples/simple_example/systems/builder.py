"""System prompt builder."""

from pathlib import Path
from typing import Any


async def build_system_prompt(context: dict[str, Any]) -> str:
    """Build system prompt from context."""
    workspace_path = context["workspace_path"]
    skills_index = context["skills_index"]
    current_time = context["current_time"]

    parts: list[str] = []

    agent_md = Path(workspace_path) / "AGENT.md"
    if agent_md.exists():
        parts.append(agent_md.read_text())

    if skills_index:
        parts.append("\n\nAvailable skills:")
        for skill in skills_index:
            parts.append(f"- {skill['name']}: {skill['description']}")
            parts.append(f"  Read SKILL.md for details: workspace/skills/{skill['name']}/SKILL.md")

    parts.append(f"\n\nCurrent time: {current_time}")

    return "\n".join(parts)


async def trim_history(messages: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Trim history if it exceeds limit (simple truncation).

    Note: This example implementation uses message count approximation.
    Real implementations should use token counting (e.g., tiktoken).
    """
    max_messages = min(20, limit // 500)  # Approximate: ~500 tokens per message
    if len(messages) > max_messages:
        return messages[:1] + messages[-(max_messages - 1) :]
    return messages
