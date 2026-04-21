"""Shared protocol definitions for Psi Agent Platform."""

from typing import Any

from pydantic import BaseModel


class LLMRequest(BaseModel):
    """Request to LLM Caller (OpenAI compatible)."""

    id: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    tool_choice: str = "auto"
    stream: bool = True


class LLMResponse(BaseModel):
    """Response from LLM Caller (OpenAI compatible)."""

    id: str
    choices: list[dict[str, Any]]
    done: bool = False
    error: str | None = None


class ToolResult(BaseModel):
    """Result from tool execution."""

    success: bool
    content: str | None = None
    error: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    returncode: int | None = None


class UserMessage(BaseModel):
    """Message from user to session."""

    role: str = "user"
    content: str


class AssistantMessage(BaseModel):
    """Message from assistant to channel."""

    role: str = "assistant"
    content: str
