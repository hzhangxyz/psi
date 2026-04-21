"""Tests for psi_workspace."""

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from psi_agent.workspace import DeltaInfo, Manifest, MountInfo, WorkspaceManager

# Check if FUSE tools are available
FUSE_AVAILABLE = all(
    shutil.which(tool) for tool in ["squashfuse", "fuse-overlayfs", "mksquashfs", "unsquashfs", "fusermount"]
)


class TestDeltaInfo:
    """Test DeltaInfo pydantic model."""

    def test_delta_info_creation(self):
        """Test creating DeltaInfo."""
        entry = DeltaInfo(
            parent=None,
            tag="base",
            created_at="2026-04-21T10:00:00",
            description="initial",
        )
        assert entry.parent is None
        assert entry.tag == "base"
        assert entry.created_at == "2026-04-21T10:00:00"
        assert entry.description == "initial"

    def test_delta_info_with_parent(self):
        """Test DeltaInfo with parent."""
        entry = DeltaInfo(
            parent="uuid-parent",
            tag="v1",
            created_at="2026-04-21T11:00:00",
            description="update",
        )
        assert entry.parent == "uuid-parent"

    def test_delta_info_json(self):
        """Test DeltaInfo JSON serialization."""
        entry = DeltaInfo(parent=None, tag="base", created_at="2026-04-21T10:00:00", description="initial")
        json_data = entry.model_dump_json()
        parsed = json.loads(json_data)
        assert parsed["parent"] is None
        assert parsed["tag"] == "base"


class TestManifest:
    """Test Manifest pydantic model."""

    def test_manifest_creation(self):
        """Test creating Manifest."""
        delta = DeltaInfo(parent=None, tag="base", created_at="2026-04-21T10:00:00", description="initial")
        manifest = Manifest(default_version="uuid-aaaa", deltas={"uuid-aaaa": delta})
        assert manifest.default_version == "uuid-aaaa"
        assert len(manifest.deltas) == 1

    def test_manifest_with_multiple_deltas(self):
        """Test Manifest with multiple deltas."""
        delta1 = DeltaInfo(parent=None, tag="base", created_at="2026-01-01", description="initial")
        delta2 = DeltaInfo(parent="uuid-aaaa", tag="v1", created_at="2026-02-01", description="update")
        manifest = Manifest(default_version="uuid-bbbb", deltas={"uuid-aaaa": delta1, "uuid-bbbb": delta2})
        assert len(manifest.deltas) == 2
        assert manifest.deltas["uuid-bbbb"].parent == "uuid-aaaa"

    def test_manifest_json_roundtrip(self):
        """Test manifest JSON serialization and deserialization."""
        delta = DeltaInfo(parent=None, tag="base", created_at="2026-01-01", description="initial")
        manifest = Manifest(default_version="uuid-aaaa", deltas={"uuid-aaaa": delta})
        json_str = manifest.model_dump_json(indent=2)
        parsed = Manifest.model_validate(json.loads(json_str))
        assert parsed.default_version == "uuid-aaaa"
        assert parsed.deltas["uuid-aaaa"].tag == "base"


class TestMountInfo:
    """Test MountInfo pydantic model."""

    def test_mount_info_creation(self):
        """Test creating MountInfo."""
        info = MountInfo(
            squashfs_path="/path/to/base.sqfs",
            current_version="uuid-aaaa",
            workspace_name="myworkspace",
            mounted_at="2026-04-21T10:00:00",
        )
        assert info.squashfs_path == "/path/to/base.sqfs"
        assert info.current_version == "uuid-aaaa"
        assert info.workspace_name == "myworkspace"


class TestWorkspaceManager:
    """Test WorkspaceManager helper methods."""

    def test_manager_init(self):
        """Test manager initialization."""
        manager = WorkspaceManager()
        assert manager._generate_uuid() is not None

    def test_generate_uuid_format(self):
        """Test UUID format (hex without dashes)."""
        manager = WorkspaceManager()
        uuid_str = manager._generate_uuid()
        assert len(uuid_str) == 32  # UUID hex without dashes
        assert "-" not in uuid_str

    def test_build_parent_chain_single(self):
        """Test building parent chain for orphan delta."""
        manager = WorkspaceManager()
        delta = DeltaInfo(parent=None, tag="base", created_at="2026-01-01", description="initial")
        manifest = Manifest(default_version="uuid-aaaa", deltas={"uuid-aaaa": delta})
        chain = manager._build_parent_chain(manifest, "uuid-aaaa")
        assert chain == ["uuid-aaaa"]

    def test_build_parent_chain_with_parent(self):
        """Test building parent chain with parent link."""
        manager = WorkspaceManager()
        delta1 = DeltaInfo(parent=None, tag="base", created_at="2026-01-01", description="initial")
        delta2 = DeltaInfo(parent="uuid-aaaa", tag="v1", created_at="2026-02-01", description="update")
        delta3 = DeltaInfo(parent="uuid-bbbb", tag="v2", created_at="2026-03-01", description="update2")
        manifest = Manifest(
            default_version="uuid-cccc",
            deltas={"uuid-aaaa": delta1, "uuid-bbbb": delta2, "uuid-cccc": delta3},
        )
        chain = manager._build_parent_chain(manifest, "uuid-cccc")
        assert chain == ["uuid-cccc", "uuid-bbbb", "uuid-aaaa"]


@pytest.mark.skipif(not FUSE_AVAILABLE, reason="FUSE tools not available")
class TestWorkspaceFUSE:
    """Test WorkspaceManager with FUSE operations."""

    @pytest.mark.asyncio
    async def test_create_squashfs(self):
        """Test creating initial squashfs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir) / "source"
            output_path = Path(tmpdir) / "base.sqfs"

            source_dir.mkdir()
            (source_dir / "AGENT.md").write_text("You are a helpful assistant.")
            tools_dir = source_dir / "tools"
            tools_dir.mkdir()
            (tools_dir / "test.py").write_text("async def run(): pass")

            manager = WorkspaceManager()
            await manager.create(str(source_dir), str(output_path), tag="base", description="initial")

            assert output_path.exists()
            assert output_path.stat().st_size > 0

    @pytest.mark.asyncio
    async def test_mount_and_umount(self):
        """Test mounting and unmounting a workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir) / "source"
            squashfs_path = Path(tmpdir) / "base.sqfs"
            workspace_dir = Path(tmpdir) / "workspace"

            source_dir.mkdir()
            (source_dir / "AGENT.md").write_text("Test agent")

            manager = WorkspaceManager()
            await manager.create(str(source_dir), str(squashfs_path), tag="base")
            await manager.mount(str(squashfs_path), str(workspace_dir))

            assert workspace_dir.exists()
            assert (workspace_dir / "AGENT.md").exists()
            assert (workspace_dir / "AGENT.md").read_text() == "Test agent"

            await manager.umount(str(workspace_dir))

    @pytest.mark.asyncio
    async def test_write_and_snapshot_new_file(self):
        """Test writing and creating snapshot to new file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir) / "source"
            squashfs_path = Path(tmpdir) / "base.sqfs"
            workspace_dir = Path(tmpdir) / "workspace"
            snapshot_path = Path(tmpdir) / "v2.sqfs"

            source_dir.mkdir()
            (source_dir / "AGENT.md").write_text("Test agent")

            manager = WorkspaceManager()
            await manager.create(str(source_dir), str(squashfs_path), tag="base")
            await manager.mount(str(squashfs_path), str(workspace_dir))

            (workspace_dir / "new_file.txt").write_text("New content")

            await manager.snapshot(str(workspace_dir), str(snapshot_path), tag="v1", description="added file")

            assert snapshot_path.exists()

            await manager.umount(str(workspace_dir))

            # Mount snapshot and verify complete view
            workspace2_dir = Path(tmpdir) / "workspace2"
            await manager.mount(str(snapshot_path), str(workspace2_dir))

            assert (workspace2_dir / "AGENT.md").exists()
            assert (workspace2_dir / "new_file.txt").exists()
            assert (workspace2_dir / "new_file.txt").read_text() == "New content"

            await manager.umount(str(workspace2_dir))

    @pytest.mark.asyncio
    async def test_snapshot_overwrite_original(self):
        """Test snapshot overwriting original file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir) / "source"
            squashfs_path = Path(tmpdir) / "base.sqfs"
            workspace_dir = Path(tmpdir) / "workspace"

            source_dir.mkdir()
            (source_dir / "AGENT.md").write_text("Test agent")

            manager = WorkspaceManager()
            await manager.create(str(source_dir), str(squashfs_path), tag="base")
            await manager.mount(str(squashfs_path), str(workspace_dir))

            (workspace_dir / "file1.txt").write_text("Content 1")

            # First snapshot (new file)
            await manager.snapshot(str(workspace_dir), str(squashfs_path), tag="v1")
            await manager.umount(str(workspace_dir))

            # Mount and make more changes
            await manager.mount(str(squashfs_path), str(workspace_dir))
            assert (workspace_dir / "file1.txt").exists()

            (workspace_dir / "file2.txt").write_text("Content 2")

            # Second snapshot (overwrite)
            await manager.snapshot(str(workspace_dir), tag="v2")
            await manager.umount(str(workspace_dir))

            # Mount and verify both files
            await manager.mount(str(squashfs_path), str(workspace_dir))
            assert (workspace_dir / "AGENT.md").exists()
            assert (workspace_dir / "file1.txt").exists()
            assert (workspace_dir / "file2.txt").exists()

            await manager.umount(str(workspace_dir))

    @pytest.mark.asyncio
    async def test_mount_specific_version(self):
        """Test mounting a specific version."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir) / "source"
            squashfs_path = Path(tmpdir) / "base.sqfs"
            workspace_dir = Path(tmpdir) / "workspace"
            v2_path = Path(tmpdir) / "v2.sqfs"

            source_dir.mkdir()
            (source_dir / "AGENT.md").write_text("Original")

            manager = WorkspaceManager()
            await manager.create(str(source_dir), str(squashfs_path), tag="base")
            await manager.mount(str(squashfs_path), str(workspace_dir))

            (workspace_dir / "AGENT.md").write_text("Modified")

            await manager.snapshot(str(workspace_dir), str(v2_path), tag="v1")
            await manager.umount(str(workspace_dir))

            # Mount original version of v2.sqfs
            # First we need to read the manifest to get the base UUID
            import json

            lower_tmp = Path(tmpdir) / "tmp_lower"
            lower_tmp.mkdir()
            await manager._mount_squashfs(v2_path, lower_tmp)
            manifest = Manifest.model_validate(json.loads((lower_tmp / "manifest.json").read_text()))
            base_uuid = [k for k, v in manifest.deltas.items() if v.parent is None][0]
            await manager._unmount_fuse(lower_tmp)
            lower_tmp.rmdir()

            # Mount base version
            await manager.mount(str(v2_path), str(workspace_dir), version=base_uuid)
            assert (workspace_dir / "AGENT.md").read_text() == "Original"

            await manager.umount(str(workspace_dir))

            # Mount default version
            await manager.mount(str(v2_path), str(workspace_dir))
            assert (workspace_dir / "AGENT.md").read_text() == "Modified"

            await manager.umount(str(workspace_dir))

    @pytest.mark.asyncio
    async def test_mount_nonexistent_squashfs(self):
        """Test mounting nonexistent squashfs fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_dir = Path(tmpdir) / "workspace"
            manager = WorkspaceManager()

            with pytest.raises(RuntimeError, match="Squashfs not found"):
                await manager.mount(str(Path(tmpdir) / "nonexistent.sqfs"), str(workspace_dir))

    @pytest.mark.asyncio
    async def test_create_from_nonexistent_source(self):
        """Test creating from nonexistent source fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkspaceManager()

            with pytest.raises(RuntimeError, match="Source directory not found"):
                await manager.create(str(Path(tmpdir) / "nonexistent"), str(Path(tmpdir) / "out.sqfs"))

    @pytest.mark.asyncio
    async def test_snapshot_without_changes(self):
        """Test snapshot when no changes exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir) / "source"
            squashfs_path = Path(tmpdir) / "base.sqfs"
            workspace_dir = Path(tmpdir) / "workspace"

            source_dir.mkdir()
            (source_dir / "AGENT.md").write_text("Test")

            manager = WorkspaceManager()
            await manager.create(str(source_dir), str(squashfs_path))
            await manager.mount(str(squashfs_path), str(workspace_dir))

            # Snapshot without changes should return early
            await manager.snapshot(str(workspace_dir))

            await manager.umount(str(workspace_dir))


@pytest.mark.skipif(not FUSE_AVAILABLE, reason="FUSE tools not available")
class TestRunFunctions:
    """Test run_* Python API functions."""

    @pytest.mark.asyncio
    async def test_run_create(self):
        """Test run_create function."""
        from psi_agent.workspace import run_create

        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir) / "source"
            output_path = Path(tmpdir) / "base.sqfs"

            source_dir.mkdir()
            (source_dir / "AGENT.md").write_text("Test")

            await run_create(str(source_dir), str(output_path), tag="base", log_level="ERROR")

            assert output_path.exists()

    @pytest.mark.asyncio
    async def test_run_mount_umount(self):
        """Test run_mount and run_umount functions."""
        from psi_agent.workspace import run_create, run_mount, run_umount

        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir) / "source"
            squashfs_path = Path(tmpdir) / "base.sqfs"
            workspace_dir = Path(tmpdir) / "workspace"

            source_dir.mkdir()
            (source_dir / "AGENT.md").write_text("Test")

            await run_create(str(source_dir), str(squashfs_path), log_level="ERROR")
            await run_mount(str(squashfs_path), str(workspace_dir), log_level="ERROR")
            assert (workspace_dir / "AGENT.md").exists()
            await run_umount(str(workspace_dir), log_level="ERROR")

    @pytest.mark.asyncio
    async def test_run_snapshot(self):
        """Test run_snapshot function."""
        from psi_agent.workspace import run_create, run_mount, run_snapshot, run_umount

        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir) / "source"
            squashfs_path = Path(tmpdir) / "base.sqfs"
            workspace_dir = Path(tmpdir) / "workspace"
            snapshot_path = Path(tmpdir) / "v1.sqfs"

            source_dir.mkdir()
            (source_dir / "AGENT.md").write_text("Test")

            await run_create(str(source_dir), str(squashfs_path), log_level="ERROR")
            await run_mount(str(squashfs_path), str(workspace_dir), log_level="ERROR")

            (workspace_dir / "new.txt").write_text("new content")

            await run_snapshot(str(workspace_dir), str(snapshot_path), tag="v1", log_level="ERROR")
            assert snapshot_path.exists()

            await run_umount(str(workspace_dir), log_level="ERROR")
