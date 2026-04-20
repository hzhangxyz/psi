"""Tests for psi_workspace."""

import json
import tempfile
from pathlib import Path

from psi_workspace import Manifest, SnapshotEntry, WorkspaceManager


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
