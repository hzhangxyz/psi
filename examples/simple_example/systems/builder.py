"""System prompt builder."""

from typing import Any


async def build_system_prompt(context: dict[str, Any]) -> str:
    """Build system prompt from context."""
    workspace_path = context["workspace_path"]
    skills_index = context["skills_index"]
    current_time = context["current_time"]

    from pathlib import Path

    parts: list[str] = []

    # Read AGENT.md
    agent_md = Path(workspace_path) / "AGENT.md"
    if agent_md.exists():
        parts.append(agent_md.read_text())

    # Add skills index
    if skills_index:
        parts.append("\n\nAvailable skills:")
        for skill in skills_index:
            parts.append(f"- {skill['name']}: {skill['description']}")
            parts.append(f"  Read SKILL.md for details: workspace/skills/{skill['name']}/SKILL.md")

    # Add current time
    parts.append(f"\n\nCurrent time: {current_time}")

    return "\n".join(parts)


async def trim_history(messages: list[dict[str, Any]], _limit: int) -> list[dict[str, Any]]:
    """Trim history if it exceeds limit (simple truncation).

    Note: This example implementation uses message count approximation.
    Real implementations should use token counting (e.g., tiktoken).
    """
    # Keep last N messages (limit is token limit, we approximate)
    max_messages = 20  # Approximate: assume ~500 tokens per message
    if len(messages) > max_messages:
        # Keep first message (usually important) and last N-1
        return messages[:1] + messages[-(max_messages - 1) :]
    return messages
