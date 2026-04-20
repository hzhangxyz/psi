"""Shared protocol definitions for Psi Agent Platform."""

from dataclasses import dataclass
from typing import Any


@dataclass
class LLMRequest:
    """Request to LLM Caller (OpenAI compatible)."""
    id: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    tool_choice: str = "auto"


@dataclass
class LLMResponse:
    """Response from LLM Caller (OpenAI compatible)."""
    id: str
    choices: list[dict[str, Any]]