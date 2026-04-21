"""Tests for psi_workspace."""

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from psi_workspace import Manifest, SnapshotEntry, WorkspaceManager

# Check if FUSE tools are available
FUSE_AVAILABLE = all(shutil.which(tool) for tool in ["squashfuse", "fuse-overlayfs", "mksquashfs", "fusermount"])


class TestSnapshotEntry:
    """Test SnapshotEntry pydantic model."""

    def test_snapshot_entry_creation(self):
        """Test creating SnapshotEntry."""
        entry = SnapshotEntry(
            name="v1.sqfs",
            description="first snapshot",
            created_at="2026-04-20T10:00:00",
        )
        assert entry.name == "v1.sqfs"
        assert entry.description == "first snapshot"
        assert entry.created_at == "2026-04-20T10:00:00"

    def test_snapshot_entry_json(self):
        """Test SnapshotEntry JSON serialization."""
        entry = SnapshotEntry(
            name="v1.sqfs",
            description="first",
            created_at="2026-04-20T10:00:00",
        )
        json_data = entry.model_dump_json()
        parsed = json.loads(json_data)
        assert parsed["name"] == "v1.sqfs"


class TestManifest:
    """Test Manifest pydantic model."""

    def test_manifest_empty(self):
        """Test empty manifest."""
        manifest = Manifest()
        assert manifest.current is None
        assert manifest.snapshots == []

    def test_manifest_with_current(self):
        """Test manifest with current workspace."""
        manifest = Manifest(
            current={"name": "base.sqfs", "status": "mounted"},
        )
        assert manifest.current is not None
        assert manifest.current["name"] == "base.sqfs"

    def test_manifest_with_snapshots(self):
        """Test manifest with snapshots."""
        entry1 = SnapshotEntry(name="v1.sqfs", description="first", created_at="2026-01-01")
        entry2 = SnapshotEntry(name="v2.sqfs", description="second", created_at="2026-02-01")
        manifest = Manifest(snapshots=[entry1, entry2])
        assert len(manifest.snapshots) == 2
        assert manifest.snapshots[0].name == "v1.sqfs"

    def test_manifest_json_roundtrip(self):
        """Test manifest JSON serialization and deserialization."""
        entry = SnapshotEntry(name="v1.sqfs", description="first", created_at="2026-01-01")
        manifest = Manifest(
            current={"name": "base.sqfs", "status": "mounted"},
            snapshots=[entry],
        )
        json_str = manifest.model_dump_json(indent=2)
        parsed = Manifest.model_validate(json.loads(json_str))
        assert parsed.current is not None
        assert parsed.current["name"] == "base.sqfs"
        assert len(parsed.snapshots) == 1


class TestWorkspaceManager:
    """Test WorkspaceManager."""

    def test_manager_init(self):
        """Test manager initialization."""
        manager = WorkspaceManager()
        assert manager.manifest_file == "manifest.json"

    def test_update_manifest_new(self):
        """Test updating a new manifest."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manager = WorkspaceManager()
            manager._update_manifest(manifest_path, "base.sqfs", "mounted")

            assert manifest_path.exists()
            data = json.loads(manifest_path.read_text())
            assert data["current"]["name"] == "base.sqfs"
            assert data["current"]["status"] == "mounted"

    def test_update_manifest_existing(self):
        """Test updating an existing manifest."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            # Create initial manifest
            initial = Manifest(snapshots=[SnapshotEntry(name="v1.sqfs", description="first", created_at="2026-01-01")])
            manifest_path.write_text(initial.model_dump_json(indent=2))

            manager = WorkspaceManager()
            manager._update_manifest(manifest_path, "v2.sqfs", "mounted")

            data = json.loads(manifest_path.read_text())
            assert data["current"]["name"] == "v2.sqfs"
            assert len(data["snapshots"]) == 1  # Existing snapshots preserved

    def test_add_snapshot(self):
        """Test adding a snapshot entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manager = WorkspaceManager()
            manager._add_snapshot(manifest_path, "v1.sqfs", "first snapshot")

            data = json.loads(manifest_path.read_text())
            assert len(data["snapshots"]) == 1
            assert data["snapshots"][0]["name"] == "v1.sqfs"
            assert data["snapshots"][0]["description"] == "first snapshot"

    def test_add_multiple_snapshots(self):
        """Test adding multiple snapshots."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manager = WorkspaceManager()

            manager._add_snapshot(manifest_path, "v1.sqfs", "first")
            manager._add_snapshot(manifest_path, "v2.sqfs", "second")

            data = json.loads(manifest_path.read_text())
            assert len(data["snapshots"]) == 2
            assert data["snapshots"][0]["name"] == "v1.sqfs"
            assert data["snapshots"][1]["name"] == "v2.sqfs"


# ============================================================================
# FUSE Integration Tests (require squashfuse, fuse-overlayfs, mksquashfs)
# ============================================================================


@pytest.mark.skipif(not FUSE_AVAILABLE, reason="FUSE tools not available")
class TestWorkspaceFUSE:
    """Test WorkspaceManager with FUSE operations."""

    @pytest.mark.asyncio
    async def test_create_squashfs(self):
        """Test creating a SquashFS image from a directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir) / "source"
            output_path = Path(tmpdir) / "base.sqfs"

            # Create source directory with some files
            source_dir.mkdir()
            (source_dir / "AGENT.md").write_text("You are a helpful assistant.")
            tools_dir = source_dir / "tools"
            tools_dir.mkdir()
            (tools_dir / "test.py").write_text("async def run(): pass")

            manager = WorkspaceManager()
            await manager.create(str(source_dir), str(output_path))

            assert output_path.exists()
            assert output_path.stat().st_size > 0

            # Verify manifest was created
            manifest_path = Path(tmpdir) / "manifest.json"
            assert manifest_path.exists()

    @pytest.mark.asyncio
    async def test_mount_and_unmount(self):
        """Test mounting and unmounting a workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir) / "source"
            squashfs_path = Path(tmpdir) / "base.sqfs"
            workspace_dir = Path(tmpdir) / "workspace"

            # Create source and squashfs
            source_dir.mkdir()
            (source_dir / "AGENT.md").write_text("Test agent")
            tools_dir = source_dir / "tools"
            tools_dir.mkdir()
            (tools_dir / "echo.py").write_text("# echo tool")

            manager = WorkspaceManager()
            await manager.create(str(source_dir), str(squashfs_path))

            # Mount
            await manager.mount(str(squashfs_path), str(workspace_dir))

            # Check workspace exists and has content
            assert workspace_dir.exists()
            assert (workspace_dir / "AGENT.md").exists()
            assert (workspace_dir / "AGENT.md").read_text() == "Test agent"
            assert (workspace_dir / "tools" / "echo.py").exists()

            # Unmount
            await manager.unmount(str(workspace_dir))

    @pytest.mark.asyncio
    async def test_write_and_snapshot(self):
        """Test writing to workspace and creating snapshot."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir) / "source"
            squashfs_path = Path(tmpdir) / "base.sqfs"
            workspace_dir = Path(tmpdir) / "workspace"
            snapshot_path = Path(tmpdir) / "v1.sqfs"

            # Create source and squashfs
            source_dir.mkdir()
            (source_dir / "AGENT.md").write_text("Test agent")

            manager = WorkspaceManager()
            await manager.create(str(source_dir), str(squashfs_path))
            await manager.mount(str(squashfs_path), str(workspace_dir))

            # Write new content
            (workspace_dir / "new_file.txt").write_text("This is new content")
            tools_dir = workspace_dir / "tools"
            tools_dir.mkdir()
            (tools_dir / "new_tool.py").write_text("# new tool")

            # Create snapshot
            await manager.snapshot(str(workspace_dir), str(snapshot_path), "added new content")

            # Check snapshot exists
            assert snapshot_path.exists()
            assert snapshot_path.stat().st_size > 0

            # Unmount
            await manager.unmount(str(workspace_dir))

            # Mount snapshot and verify changes
            workspace2_dir = Path(tmpdir) / "workspace2"
            await manager.mount(str(snapshot_path), str(workspace2_dir))

            assert (workspace2_dir / "new_file.txt").exists()
            assert (workspace2_dir / "new_file.txt").read_text() == "This is new content"
            assert (workspace2_dir / "tools" / "new_tool.py").exists()

            await manager.unmount(str(workspace2_dir))

    @pytest.mark.asyncio
    async def test_mount_nonexistent_squashfs(self):
        """Test mounting a nonexistent SquashFS fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_dir = Path(tmpdir) / "workspace"
            manager = WorkspaceManager()

            with pytest.raises(RuntimeError):
                await manager.mount(str(Path(tmpdir) / "nonexistent.sqfs"), str(workspace_dir))

    @pytest.mark.asyncio
    async def test_create_from_nonexistent_source(self):
        """Test creating from nonexistent source fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkspaceManager()

            with pytest.raises(RuntimeError):
                await manager.create(str(Path(tmpdir) / "nonexistent"), str(Path(tmpdir) / "out.sqfs"))

    @pytest.mark.asyncio
    async def test_snapshot_without_changes(self):
        """Test snapshot when no changes exist (upper_dir missing)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a fake workspace structure without upper_dir
            workspace_dir = Path(tmpdir) / "workspace"
            workspace_dir.mkdir()
            snapshot_path = Path(tmpdir) / "empty.sqfs"

            manager = WorkspaceManager()
            # Should return early without creating snapshot
            await manager.snapshot(str(workspace_dir), str(snapshot_path))

            # Snapshot should not exist
            assert not snapshot_path.exists()

    def test_list_snapshots_empty(self, capsys):
        """Test listing snapshots when manifest doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_dir = Path(tmpdir) / "workspace"
            workspace_dir.mkdir()

            manager = WorkspaceManager()
            manager.list_snapshots(str(workspace_dir))

            captured = capsys.readouterr()
            assert "No snapshots found" in captured.out

    def test_list_snapshots_with_entries(self, capsys):
        """Test listing snapshots with entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_dir = Path(tmpdir) / "workspace"
            workspace_dir.mkdir()

            manifest_path = Path(tmpdir) / "manifest.json"
            manager = WorkspaceManager()
            manager._add_snapshot(manifest_path, "v1.sqfs", "first snapshot")
            manager._add_snapshot(manifest_path, "v2.sqfs", "second snapshot")

            manager.list_snapshots(str(workspace_dir))

            captured = capsys.readouterr()
            assert "v1.sqfs" in captured.out
            assert "v2.sqfs" in captured.out
            assert "first snapshot" in captured.out
            assert "second snapshot" in captured.out

    @pytest.mark.asyncio
    async def test_unmount_without_lower(self):
        """Test unmount when lower_dir doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir) / "source"
            squashfs_path = Path(tmpdir) / "base.sqfs"
            workspace_dir = Path(tmpdir) / "workspace"

            source_dir.mkdir()
            (source_dir / "AGENT.md").write_text("Test")

            manager = WorkspaceManager()
            await manager.create(str(source_dir), str(squashfs_path))
            await manager.mount(str(squashfs_path), str(workspace_dir))

            # Unmount should work even if lower_dir check fails
            await manager.unmount(str(workspace_dir))
