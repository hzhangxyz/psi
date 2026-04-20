"""Psi Workspace - SquashFS/OverlayFS manager."""

import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import tyro
from loguru import logger
from pydantic import BaseModel


class SnapshotEntry(BaseModel):
    """Snapshot metadata entry."""

    name: str
    description: str
    created_at: str


class Manifest(BaseModel):
    """Workspace manifest."""

    current: dict[str, Any] | None = None
    snapshots: list[SnapshotEntry] = []


class WorkspaceManager:
    """Manages SquashFS and OverlayFS for workspace snapshots."""

    manifest_file: str

    def __init__(self) -> None:
        self.manifest_file = "manifest.json"
        logger.debug("WorkspaceManager initialized")

    async def mount(self, squashfs_path: str, output_dir: str) -> None:
        """Mount a SquashFS image as a writable workspace using OverlayFS."""
        squashfs = Path(squashfs_path).resolve()
        workspace = Path(output_dir).resolve()

        logger.info(f"Mounting workspace | squashfs={squashfs} | output={workspace}")

        # Create directories
        upper_dir = Path(str(workspace) + ".upper")
        work_dir = Path(str(workspace) + ".work")
        lower_dir = Path(str(workspace) + ".lower")

        upper_dir.mkdir(parents=True, exist_ok=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        lower_dir.mkdir(parents=True, exist_ok=True)

        logger.debug(f"Directories created | upper={upper_dir} | work={work_dir} | lower={lower_dir}")

        # Mount squashfs to lower
        logger.debug(f"Mounting squashfs | source={squashfs} | target={lower_dir}")
        proc = await asyncio.create_subprocess_exec(
            "mount",
            "-t",
            "squashfs",
            str(squashfs),
            str(lower_dir),
        )
        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"mount squashfs failed with code {proc.returncode}")
        logger.info(f"SquashFS mounted | path={lower_dir}")

        # Mount overlayfs
        logger.debug(f"Mounting overlayfs | lower={lower_dir} | upper={upper_dir} | work={work_dir}")
        proc = await asyncio.create_subprocess_exec(
            "mount",
            "-t",
            "overlay",
            "overlay",
            "-o",
            f"lowerdir={lower_dir},upperdir={upper_dir},workdir={work_dir}",
            str(workspace),
        )
        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"mount overlayfs failed with code {proc.returncode}")
        logger.info(f"OverlayFS mounted | path={workspace}")

        # Create/update manifest
        manifest_path = workspace.parent / self.manifest_file
        self._update_manifest(manifest_path, squashfs.name, "mounted")

        logger.info(f"Workspace mounted successfully | squashfs={squashfs_path} | workspace={output_dir}")

    async def unmount(self, workspace_dir: str) -> None:
        """Unmount a workspace and clean up."""
        workspace = Path(workspace_dir).resolve()

        logger.info(f"Unmounting workspace | path={workspace}")

        # Unmount overlayfs
        logger.debug(f"Unmounting overlayfs | path={workspace}")
        proc = await asyncio.create_subprocess_exec("umount", str(workspace))
        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"umount overlayfs failed with code {proc.returncode}")
        logger.info(f"OverlayFS unmounted | path={workspace}")

        # Unmount squashfs (lower)
        lower_dir = Path(str(workspace) + ".lower")
        if lower_dir.exists():
            logger.debug(f"Unmounting squashfs | path={lower_dir}")
            proc = await asyncio.create_subprocess_exec("umount", str(lower_dir))
            await proc.wait()
            if proc.returncode != 0:
                raise RuntimeError(f"umount squashfs failed with code {proc.returncode}")
            logger.info(f"SquashFS unmounted | path={lower_dir}")

        logger.info(f"Workspace unmounted successfully | workspace={workspace_dir}")

    async def snapshot(
        self,
        workspace_dir: str,
        output_path: str,
        description: str = "",
    ) -> None:
        """Create a new SquashFS snapshot from workspace changes."""
        workspace = Path(workspace_dir).resolve()
        upper_dir = Path(str(workspace) + ".upper")
        output = Path(output_path).resolve()

        logger.info(f"Creating snapshot | workspace={workspace} | output={output} | description={description}")

        if not upper_dir.exists():
            logger.warning(f"No changes to snapshot | upper_dir={upper_dir} not found")
            print("No changes to snapshot (upper dir not found)", file=sys.stderr)
            return

        # Create squashfs from upper directory
        logger.debug(f"Creating squashfs | source={upper_dir} | output={output}")
        proc = await asyncio.create_subprocess_exec(
            "mksquashfs",
            str(upper_dir),
            str(output),
            "-noappend",
        )
        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"mksquashfs failed with code {proc.returncode}")
        logger.info(f"SquashFS created | path={output}")

        # Update manifest with snapshot history
        manifest_path = workspace.parent / self.manifest_file
        self._add_snapshot(manifest_path, output.name, description)

        logger.info(f"Snapshot created successfully | output={output_path}")

    def list_snapshots(self, workspace_dir: str) -> None:
        """List all snapshots for a workspace."""
        workspace = Path(workspace_dir).resolve()
        manifest_path = workspace.parent / self.manifest_file

        logger.debug(f"Listing snapshots | workspace={workspace} | manifest={manifest_path}")

        if not manifest_path.exists():
            logger.warning(f"Manifest not found | path={manifest_path}")
            print("No snapshots found")
            return

        manifest_data = json.loads(manifest_path.read_text())
        manifest = Manifest.model_validate(manifest_data)
        snapshots = manifest.snapshots

        logger.info(f"Found {len(snapshots)} snapshots")

        print(f"Snapshots for {workspace_dir}:")
        for snap in snapshots:
            print(f"  - {snap.name}: {snap.description} ({snap.created_at})")
            logger.debug(f"Snapshot | name={snap.name} | created_at={snap.created_at}")

    def _update_manifest(self, manifest_path: Path, name: str, status: str) -> None:
        """Update manifest file."""
        logger.debug(f"Updating manifest | path={manifest_path} | name={name} | status={status}")

        if manifest_path.exists():
            manifest_data = json.loads(manifest_path.read_text())
            manifest = Manifest.model_validate(manifest_data)
        else:
            manifest = Manifest()

        manifest.current = {
            "name": name,
            "status": status,
            "mounted_at": datetime.now().isoformat(),
        }

        manifest_path.write_text(manifest.model_dump_json(indent=2))
        logger.debug(f"Manifest updated | current={manifest.current}")

    def _add_snapshot(self, manifest_path: Path, name: str, description: str) -> None:
        """Add snapshot entry to manifest."""
        logger.debug(f"Adding snapshot to manifest | path={manifest_path} | name={name}")

        if manifest_path.exists():
            manifest_data = json.loads(manifest_path.read_text())
            manifest = Manifest.model_validate(manifest_data)
        else:
            manifest = Manifest()

        snapshot_entry = SnapshotEntry(
            name=name,
            description=description,
            created_at=datetime.now().isoformat(),
        )

        manifest.snapshots.append(snapshot_entry)
        manifest_path.write_text(manifest.model_dump_json(indent=2))

        logger.debug(f"Snapshot entry added | snapshot={snapshot_entry}")


async def run_mount(squashfs_path: str, output_dir: str, log_level: str = "INFO") -> None:
    """Python function interface for mount."""
    _setup_logger(log_level)
    manager = WorkspaceManager()
    await manager.mount(squashfs_path, output_dir)


async def run_unmount(workspace_dir: str, log_level: str = "INFO") -> None:
    """Python function interface for unmount."""
    _setup_logger(log_level)
    manager = WorkspaceManager()
    await manager.unmount(workspace_dir)


async def run_snapshot(
    workspace_dir: str,
    output_path: str,
    description: str = "",
    log_level: str = "INFO",
) -> None:
    """Python function interface for snapshot."""
    _setup_logger(log_level)
    manager = WorkspaceManager()
    await manager.snapshot(workspace_dir, output_path, description)


def run_list(workspace_dir: str, log_level: str = "INFO") -> None:
    """Python function interface for list."""
    _setup_logger(log_level)
    manager = WorkspaceManager()
    manager.list_snapshots(workspace_dir)


def _setup_logger(log_level: str) -> None:
    """Configure logger."""
    logger.remove()
    logger.add(
        lambda msg: print(msg, end=""),
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>workspace</cyan> | {message}",
        level=log_level,
    )


@dataclass
class MountArgs:
    """Mount SquashFS as workspace."""

    squashfs: str
    """SquashFS image path"""

    output: str
    """Output workspace directory"""

    log_level: str = "INFO"
    """Log level (DEBUG, INFO, WARNING, ERROR)"""


@dataclass
class UnmountArgs:
    """Unmount workspace."""

    workspace: str
    """Workspace directory"""

    log_level: str = "INFO"
    """Log level (DEBUG, INFO, WARNING, ERROR)"""


@dataclass
class SnapshotArgs:
    """Create snapshot."""

    workspace: str
    """Workspace directory"""

    output: str
    """Output SquashFS path"""

    description: str = ""
    """Snapshot description"""

    log_level: str = "INFO"
    """Log level (DEBUG, INFO, WARNING, ERROR)"""


@dataclass
class ListArgs:
    """List snapshots."""

    workspace: str
    """Workspace directory"""

    log_level: str = "INFO"
    """Log level (DEBUG, INFO, WARNING, ERROR)"""


@dataclass
class CliArgs:
    """Workspace CLI with subcommands."""

    mount: MountArgs | None = None
    unmount: UnmountArgs | None = None
    snapshot: SnapshotArgs | None = None
    list: ListArgs | None = None


def main() -> None:
    args = tyro.cli(CliArgs)

    if args.mount:
        asyncio.run(run_mount(args.mount.squashfs, args.mount.output, args.mount.log_level))
    elif args.unmount:
        asyncio.run(run_unmount(args.unmount.workspace, args.unmount.log_level))
    elif args.snapshot:
        asyncio.run(
            run_snapshot(
                args.snapshot.workspace, args.snapshot.output, args.snapshot.description, args.snapshot.log_level
            )
        )
    elif args.list:
        run_list(args.list.workspace, args.list.log_level)


if __name__ == "__main__":
    main()
