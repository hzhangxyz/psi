"""Tests for psi_session."""

import json
import tempfile
from pathlib import Path

import pytest

from psi_common import LLMRequest, ToolResult
from psi_session import Session


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        # Create basic structure
        (workspace / "tools").mkdir()
        (workspace / "skills").mkdir()
        (workspace / "systems").mkdir()
        (workspace / "state").mkdir()
        # Create AGENT.md
        (workspace / "AGENT.md").write_text("Test Agent")
        yield workspace


@pytest.fixture
def session(temp_workspace):
    """Create a Session instance for testing."""
    return Session(
        workspace_path=str(temp_workspace),
        channel_socket="/tmp/test-channel.sock",
        llm_socket="/tmp/test-llm.sock",
        session_id="test",
    )


class TestSessionInit:
    """Test Session initialization."""

    def test_session_attributes(self, session, temp_workspace):
        """Test session has correct attributes."""
        assert session.workspace_path == temp_workspace
        assert session.channel_socket == "/tmp/test-channel.sock"
        assert session.llm_socket == "/tmp/test-llm.sock"
        assert session.session_id == "test"
        assert session.max_iterations == 10

    def test_session_db_path(self, session, temp_workspace):
        """Test session db path is set correctly."""
        expected_db = str(temp_workspace / "state" / "session-test.db")
        assert session.db_path == expected_db


class TestSessionDB:
    """Test Session database operations."""

    @pytest.mark.asyncio
    async def test_init_db(self, session):
        """Test database initialization."""
        await session.init_db()
        assert Path(session.db_path).exists()

    @pytest.mark.asyncio
    async def test_save_and_load_message(self, session):
        """Test saving and loading messages."""
        await session.init_db()

        msg = {"role": "user", "content": "hello"}
        await session.save_message(msg)

        # Reload history
        session.messages = []
        await session.load_history()

        assert len(session.messages) == 1
        assert session.messages[0]["role"] == "user"
        assert session.messages[0]["content"] == "hello"


class TestSessionTools:
    """Test Session tool loading."""

    def test_load_empty_tools(self, session):
        """Test loading when no tools exist."""
        session.load_tools()
        assert len(session.tools) == 0

    def test_load_tools(self, session, temp_workspace):
        """Test loading tools from workspace."""
        # Create a simple tool
        tool_code = '''
async def run(params: dict, workspace_path: str) -> dict:
    """A test tool."""
    return {"success": True, "content": "test"}
'''
        (temp_workspace / "tools" / "test_tool.py").write_text(tool_code)

        session.load_tools()
        assert "test_tool" in session.tools
        assert len(session.tools_schema) == 1
        assert session.tools_schema[0]["function"]["name"] == "test_tool"


class TestSessionSkills:
    """Test Session skill loading."""

    def test_load_empty_skills(self, session):
        """Test loading when no skills exist."""
        session.load_skills()
        assert len(session.skills_index) == 0

    def test_load_skills(self, session, temp_workspace):
        """Test loading skills from workspace."""
        # Create skill directory and SKILL.md
        skill_dir = temp_workspace / "skills" / "test_skill"
        skill_dir.mkdir()
        skill_md = """---
name: test_skill
description: A test skill
---
Skill content here.
"""
        (skill_dir / "SKILL.md").write_text(skill_md)

        session.load_skills()
        assert len(session.skills_index) == 1
        assert session.skills_index[0]["name"] == "test_skill"
        assert session.skills_index[0]["description"] == "A test skill"


class TestLLMRequest:
    """Test LLMRequest pydantic model."""

    def test_llm_request_creation(self):
        """Test creating LLMRequest."""
        request = LLMRequest(
            id="test-1",
            messages=[{"role": "user", "content": "hi"}],
        )
        assert request.id == "test-1"
        assert request.stream is True

    def test_llm_request_json(self):
        """Test LLMRequest JSON serialization."""
        request = LLMRequest(
            id="test-2",
            messages=[{"role": "user", "content": "hi"}],
            stream=False,
        )
        json_data = request.model_dump_json()
        parsed = json.loads(json_data)
        assert parsed["id"] == "test-2"
        assert parsed["stream"] is False


class TestToolResult:
    """Test ToolResult pydantic model."""

    def test_success_result(self):
        """Test successful tool result."""
        result = ToolResult(success=True, content="output")
        assert result.success is True
        assert result.content == "output"

    def test_error_result(self):
        """Test error tool result."""
        result = ToolResult(success=False, error="failed")
        assert result.success is False
        assert result.error == "failed"

    def test_result_json(self):
        """Test ToolResult JSON serialization."""
        result = ToolResult(success=True, content="output")
        json_data = result.model_dump()
        assert json_data["success"] is True
        assert json_data["content"] == "output"
