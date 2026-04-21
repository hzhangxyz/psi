"""Tests for psi_common protocol models."""

from psi_agent.common.protocol import AssistantMessage, LLMRequest, LLMResponse, ToolResult, UserMessage


def test_llm_request_basic():
    """Test basic LLMRequest creation."""
    request = LLMRequest(
        id="test-1",
        messages=[{"role": "user", "content": "hello"}],
    )
    assert request.id == "test-1"
    assert request.messages == [{"role": "user", "content": "hello"}]
    assert request.stream is True
    assert request.tool_choice == "auto"


def test_llm_request_with_tools():
    """Test LLMRequest with tools."""
    tools = [{"type": "function", "function": {"name": "test"}}]
    request = LLMRequest(
        id="test-2",
        messages=[{"role": "user", "content": "hello"}],
        tools=tools,
        tool_choice="required",
        stream=False,
    )
    assert request.tools == tools
    assert request.tool_choice == "required"
    assert request.stream is False


def test_llm_request_json_serialization():
    """Test LLMRequest JSON serialization."""
    request = LLMRequest(
        id="test-3",
        messages=[{"role": "user", "content": "hello"}],
    )
    json_str = request.model_dump_json()
    assert '"id":"test-3"' in json_str
    assert '"stream":true' in json_str


def test_llm_response():
    """Test LLMResponse creation."""
    response = LLMResponse(
        id="test-1",
        choices=[{"message": {"role": "assistant", "content": "hi"}}],
    )
    assert response.id == "test-1"
    assert response.choices == [{"message": {"role": "assistant", "content": "hi"}}]
    assert response.done is False


def test_llm_response_done():
    """Test LLMResponse with done marker."""
    response = LLMResponse(
        id="test-1",
        choices=[],
        done=True,
    )
    assert response.done is True


def test_tool_result_success():
    """Test ToolResult success case."""
    result = ToolResult(
        success=True,
        content="file contents",
    )
    assert result.success is True
    assert result.content == "file contents"
    assert result.error is None


def test_tool_result_error():
    """Test ToolResult error case."""
    result = ToolResult(
        success=False,
        error="File not found",
    )
    assert result.success is False
    assert result.error == "File not found"


def test_tool_result_bash():
    """Test ToolResult with bash output."""
    result = ToolResult(
        success=True,
        stdout="output",
        stderr="",
        returncode=0,
    )
    assert result.stdout == "output"
    assert result.returncode == 0


def test_user_message():
    """Test UserMessage creation."""
    msg = UserMessage(content="hello")
    assert msg.role == "user"
    assert msg.content == "hello"


def test_assistant_message():
    """Test AssistantMessage creation."""
    msg = AssistantMessage(content="hi there")
    assert msg.role == "assistant"
    assert msg.content == "hi there"
