"""Psi Workspace - Delta-based SquashFS/OverlayFS manager using FUSE (no root required)."""

import asyncio
import json
import shutil
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import tyro
from loguru import logger
from pydantic import BaseModel


class DeltaInfo(BaseModel):
    """Delta metadata."""

    parent: str | None
    tag: str | None
    created_at: str
    description: str


class Manifest(BaseModel):
    """Workspace manifest with delta chain."""

    default_version: str
    deltas: dict[str, DeltaInfo]


class MountInfo(BaseModel):
    """Mount session info."""

    squashfs_path: str
    current_version: str
    workspace_name: str
    mounted_at: str


class WorkspaceManager:
    """Manages delta-based SquashFS workspaces using FUSE."""

    def __init__(self) -> None:
        logger.debug("WorkspaceManager initialized")

    def _generate_uuid(self) -> str:
        """Generate UUID for delta folder name."""
        return uuid.uuid4().hex

    def _build_parent_chain(self, manifest: Manifest, version: str) -> list[str]:
        """Build chain from version to orphan (newest to oldest)."""
        chain = [version]
        current = version
        while True:
            parent = manifest.deltas[current].parent
            if parent is None:
                break
            chain.append(parent)
            current = parent
        return chain

    async def _mount_squashfs(self, sqfs_path: str, target_dir: Path) -> None:
        """Mount squashfs to directory using squashfuse."""
        logger.debug(f"Mounting squashfs | source={sqfs_path} | target={target_dir}")
        proc = await asyncio.create_subprocess_exec(
            "squashfuse",
            sqfs_path,
            str(target_dir),
        )
        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"squashfuse failed with code {proc.returncode}")
        logger.info(f"Squashfs mounted | path={target_dir}")

    async def _unmount_fuse(self, mount_point: Path) -> None:
        """Unmount a FUSE mount using fusermount."""
        logger.debug(f"Unmounting FUSE | path={mount_point}")
        proc = await asyncio.create_subprocess_exec(
            "fusermount",
            "-u",
            str(mount_point),
        )
        await proc.wait()
        if proc.returncode != 0:
            logger.warning("fusermount failed, trying lazy unmount")
            proc = await asyncio.create_subprocess_exec(
                "fusermount",
                "-u",
                "-z",
                str(mount_point),
            )
            await proc.wait()
        logger.info(f"FUSE unmounted | path={mount_point}")

    async def _mount_overlay(
        self,
        lower_dirs: list[Path],
        upper_dir: Path,
        work_dir: Path,
        target_dir: Path,
    ) -> None:
        """Mount OverlayFS using fuse-overlayfs."""
        lowerdir = ":".join(str(d) for d in lower_dirs)
        logger.debug(
            f"Mounting overlay | lowerdir={lowerdir} | upper={upper_dir} | work={work_dir} | target={target_dir}"
        )
        upper_dir.mkdir(parents=True, exist_ok=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            "fuse-overlayfs",
            "-o",
            f"lowerdir={lowerdir},upperdir={upper_dir},workdir={work_dir}",
            str(target_dir),
        )
        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"fuse-overlayfs failed with code {proc.returncode}")
        logger.info(f"Overlay mounted | path={target_dir}")

    async def _unpack_squashfs(self, sqfs_path: str, target_dir: Path) -> None:
        """Unpack squashfs to directory using unsquashfs."""
        logger.debug(f"Unpacking squashfs | source={sqfs_path} | target={target_dir}")
        proc = await asyncio.create_subprocess_exec(
            "unsquashfs",
            "-d",
            str(target_dir),
            sqfs_path,
        )
        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"unsquashfs failed with code {proc.returncode}")
        logger.info(f"Squashfs unpacked | path={target_dir}")

    async def _pack_squashfs(self, source_dir: Path, output_path: Path) -> None:
        """Pack directory to squashfs using mksquashfs."""
        logger.debug(f"Packing squashfs | source={source_dir} | output={output_path}")
        proc = await asyncio.create_subprocess_exec(
            "mksquashfs",
            str(source_dir),
            str(output_path),
            "-noappend",
        )
        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"mksquashfs failed with code {proc.returncode}")
        logger.info(f"Squashfs packed | path={output_path}")

    async def create(
        self,
        source_dir: str,
        output_path: str,
        tag: str | None = None,
        description: str = "",
    ) -> None:
        """Create initial squashfs from source directory."""
        source = Path(source_dir).resolve()
        output = Path(output_path).resolve()

        logger.info(f"Creating squashfs | source={source} | output={output}")

        if not source.exists():
            raise RuntimeError(f"Source directory not found: {source}")

        delta_uuid = self._generate_uuid()
        logger.debug(f"Generated delta UUID | uuid={delta_uuid}")

        # Create temp directory with manifest + delta
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)

            # Copy source to delta folder
            delta_dir = tmp / delta_uuid
            shutil.copytree(source, delta_dir)
            logger.debug(f"Copied source to delta | delta_dir={delta_dir}")

            # Create manifest
            manifest = Manifest(
                default_version=delta_uuid,
                deltas={
                    delta_uuid: DeltaInfo(
                        parent=None,
                        tag=tag,
                        created_at=datetime.now().isoformat(),
                        description=description or f"Initial from {source.name}",
                    )
                },
            )
            manifest_file = tmp / "manifest.json"
            manifest_file.write_text(manifest.model_dump_json(indent=2))
            logger.debug(f"Created manifest | default_version={delta_uuid}")

            # Pack to squashfs
            await self._pack_squashfs(tmp, output)

        logger.info(f"Created squashfs successfully | output={output}")

    async def mount(
        self,
        squashfs_path: str,
        workspace_dir: str,
        version: str | None = None,
    ) -> None:
        """Mount squashfs as writable workspace."""
        sqfs = Path(squashfs_path).resolve()
        workspace = Path(workspace_dir).resolve()
        workspace_name = workspace.name

        logger.info(f"Mounting workspace | squashfs={sqfs} | workspace={workspace}")

        if not sqfs.exists():
            raise RuntimeError(f"Squashfs not found: {sqfs}")

        # Create .psi helper directory
        psi_dir = workspace.parent / ".psi"
        psi_dir.mkdir(parents=True, exist_ok=True)

        lower_dir = psi_dir / f"lower-{workspace_name}"
        upper_dir = psi_dir / f"upper-{workspace_name}"
        work_dir = psi_dir / f"work-{workspace_name}"

        # Mount squashfs to lower
        lower_dir.mkdir(parents=True, exist_ok=True)
        await self._mount_squashfs(str(sqfs), lower_dir)

        # Read manifest and resolve version
        manifest_file = lower_dir / "manifest.json"
        if not manifest_file.exists():
            await self._unmount_fuse(lower_dir)
            raise RuntimeError("No manifest.json in squashfs")

        manifest = Manifest.model_validate(json.loads(manifest_file.read_text()))
        current_version = version or manifest.default_version

        if current_version not in manifest.deltas:
            await self._unmount_fuse(lower_dir)
            raise RuntimeError(f"Version {current_version} not found in manifest")

        logger.debug(f"Resolved version | version={current_version}")

        # Build parent chain
        chain = self._build_parent_chain(manifest, current_version)
        logger.debug(f"Parent chain | chain={chain}")

        # Build lowerdirs (newest to oldest)
        lower_dirs = [lower_dir / uuid_name for uuid_name in chain]

        # Verify all delta directories exist
        for d in lower_dirs:
            if not d.exists():
                await self._unmount_fuse(lower_dir)
                raise RuntimeError(f"Delta directory not found: {d}")

        # Mount overlay
        workspace.mkdir(parents=True, exist_ok=True)
        await self._mount_overlay(lower_dirs, upper_dir, work_dir, workspace)

        # Write mount info
        mount_info = MountInfo(
            squashfs_path=str(sqfs),
            current_version=current_version,
            workspace_name=workspace_name,
            mounted_at=datetime.now().isoformat(),
        )
        mount_info_file = psi_dir / f"mount-{workspace_name}.json"
        mount_info_file.write_text(mount_info.model_dump_json(indent=2))
        logger.debug(f"Written mount info | path={mount_info_file}")

        logger.info(f"Mounted workspace successfully | version={current_version}")

    async def snapshot(
        self,
        workspace_dir: str,
        output_path: str | None = None,
        tag: str | None = None,
        description: str = "",
    ) -> None:
        """Create snapshot from workspace changes."""
        workspace = Path(workspace_dir).resolve()
        workspace_name = workspace.name
        psi_dir = workspace.parent / ".psi"

        logger.info(f"Creating snapshot | workspace={workspace}")

        # Read mount info
        mount_info_file = psi_dir / f"mount-{workspace_name}.json"
        if not mount_info_file.exists():
            raise RuntimeError(f"Mount info not found: {mount_info_file}")

        mount_info = MountInfo.model_validate(json.loads(mount_info_file.read_text()))
        original_sqfs = Path(mount_info.squashfs_path)

        # Determine output path
        output = Path(output_path).resolve() if output_path else original_sqfs
        logger.debug(f"Output path | output={output}")

        # Read upper directory (changes)
        upper_dir = psi_dir / f"upper-{workspace_name}"
        if not upper_dir.exists() or not any(upper_dir.iterdir()):
            logger.warning("No changes to snapshot")
            print("No changes to snapshot", file=sys.stderr)
            return

        new_uuid = self._generate_uuid()
        logger.debug(f"Generated new delta UUID | uuid={new_uuid}")

        # Unmount overlay first
        await self._unmount_fuse(workspace)
        logger.info("Overlay unmounted")

        # Unmount squashfs
        lower_dir = psi_dir / f"lower-{workspace_name}"
        await self._unmount_fuse(lower_dir)
        logger.info("Squashfs unmounted")

        # Unpack original squashfs to temp
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)

            await self._unpack_squashfs(str(original_sqfs), tmp)

            # Read existing manifest
            manifest_file = tmp / "manifest.json"
            manifest = Manifest.model_validate(json.loads(manifest_file.read_text()))

            # Copy upper changes to new delta
            new_delta_dir = tmp / new_uuid
            shutil.copytree(upper_dir, new_delta_dir)
            logger.debug(f"Copied upper to new delta | delta={new_uuid}")

            # Add new delta to manifest
            manifest.deltas[new_uuid] = DeltaInfo(
                parent=mount_info.current_version,
                tag=tag,
                created_at=datetime.now().isoformat(),
                description=description,
            )
            manifest.default_version = new_uuid

            manifest_file.write_text(manifest.model_dump_json(indent=2))
            logger.debug(f"Updated manifest | new_uuid={new_uuid}")

            # Pack new squashfs (to temp file first for safety)
            temp_output = tmp / "new.sqfs"
            await self._pack_squashfs(tmp, temp_output)

            # Move to final location
            shutil.move(str(temp_output), str(output))
            logger.info(f"Moved new squashfs to output | output={output}")

        # Remount with new squashfs
        lower_dir.mkdir(parents=True, exist_ok=True)
        await self._mount_squashfs(str(output), lower_dir)

        # Build new chain
        manifest_file = lower_dir / "manifest.json"
        manifest = Manifest.model_validate(json.loads(manifest_file.read_text()))
        chain = self._build_parent_chain(manifest, new_uuid)
        lower_dirs = [lower_dir / uuid_name for uuid_name in chain]

        # Clear upper before remount
        upper_dir.mkdir(parents=True, exist_ok=True)
        for item in upper_dir.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        logger.debug("Cleared upper directory")

        # Remount overlay
        work_dir = psi_dir / f"work-{workspace_name}"
        await self._mount_overlay(lower_dirs, upper_dir, work_dir, workspace)

        # Update mount info
        mount_info.current_version = new_uuid
        mount_info.squashfs_path = str(output)
        mount_info.mounted_at = datetime.now().isoformat()
        mount_info_file.write_text(mount_info.model_dump_json(indent=2))

        logger.info(f"Snapshot created successfully | version={new_uuid}")

    async def umount(self, workspace_dir: str) -> None:
        """Unmount workspace and optionally cleanup."""
        workspace = Path(workspace_dir).resolve()
        workspace_name = workspace.name
        psi_dir = workspace.parent / ".psi"

        logger.info(f"Unmounting workspace | workspace={workspace}")

        # Unmount overlay
        await self._unmount_fuse(workspace)
        logger.info("Overlay unmounted")

        # Unmount squashfs
        lower_dir = psi_dir / f"lower-{workspace_name}"
        if lower_dir.exists():
            await self._unmount_fuse(lower_dir)
            logger.info("Squashfs unmounted")

        # Remove mount info
        mount_info_file = psi_dir / f"mount-{workspace_name}.json"
        if mount_info_file.exists():
            mount_info_file.unlink()
            logger.debug("Removed mount info")

        # Check if other mounts exist
        other_mounts = list(psi_dir.glob("mount-*.json"))
        if not other_mounts:
            # No other mounts, clean up .psi
            if psi_dir.exists():
                shutil.rmtree(psi_dir)
            logger.info("Cleaned up .psi directory")
        else:
            logger.debug(f"Other mounts exist | count={len(other_mounts)}")

        logger.info("Unmounted successfully")


# ============================================================================
# Python API
# ============================================================================


async def run_create(
    source_dir: str,
    output_path: str,
    tag: str | None = None,
    description: str = "",
    log_level: str = "INFO",
) -> None:
    """Python API for create."""
    _setup_logger(log_level)
    manager = WorkspaceManager()
    await manager.create(source_dir, output_path, tag, description)


async def run_mount(
    squashfs_path: str,
    workspace_dir: str,
    version: str | None = None,
    log_level: str = "INFO",
) -> None:
    """Python API for mount."""
    _setup_logger(log_level)
    manager = WorkspaceManager()
    await manager.mount(squashfs_path, workspace_dir, version)


async def run_snapshot(
    workspace_dir: str,
    output_path: str | None = None,
    tag: str | None = None,
    description: str = "",
    log_level: str = "INFO",
) -> None:
    """Python API for snapshot."""
    _setup_logger(log_level)
    manager = WorkspaceManager()
    await manager.snapshot(workspace_dir, output_path, tag, description)


async def run_umount(workspace_dir: str, log_level: str = "INFO") -> None:
    """Python API for umount."""
    _setup_logger(log_level)
    manager = WorkspaceManager()
    await manager.umount(workspace_dir)


def _setup_logger(log_level: str) -> None:
    """Configure logger."""
    logger.remove()
    logger.add(
        lambda msg: print(msg, end=""),
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>workspace</cyan> | {message}",
        level=log_level,
    )


# ============================================================================
# CLI
# ============================================================================


@dataclass
class CreateArgs:
    """Create initial squashfs from directory."""

    source: str
    """Source directory."""
    output: str
    """Output squashfs path."""
    tag: str | None = None
    """Tag for this delta."""
    description: str = ""
    """Description."""
    log_level: str = "INFO"
    """Log level."""


@dataclass
class MountArgs:
    """Mount squashfs as workspace."""

    squashfs: str
    """Squashfs path."""
    workspace: str
    """Workspace directory."""
    version: str | None = None
    """Version to mount (default: use default_version)."""
    log_level: str = "INFO"
    """Log level."""


@dataclass
class SnapshotArgs:
    """Create snapshot from workspace."""

    workspace: str
    """Workspace directory."""
    output: str | None = None
    """Output squashfs path (default: overwrite original)."""
    tag: str | None = None
    """Tag for this delta."""
    description: str = ""
    """Description."""
    log_level: str = "INFO"
    """Log level."""


@dataclass
class UmountArgs:
    """Unmount workspace."""

    workspace: str
    """Workspace directory."""
    log_level: str = "INFO"
    """Log level."""


def main_create() -> None:
    """CLI for create."""
    args = tyro.cli(CreateArgs)
    asyncio.run(run_create(args.source, args.output, args.tag, args.description, args.log_level))


def main_mount() -> None:
    """CLI for mount."""
    args = tyro.cli(MountArgs)
    asyncio.run(run_mount(args.squashfs, args.workspace, args.version, args.log_level))


def main_snapshot() -> None:
    """CLI for snapshot."""
    args = tyro.cli(SnapshotArgs)
    asyncio.run(run_snapshot(args.workspace, args.output, args.tag, args.description, args.log_level))


def main_umount() -> None:
    """CLI for umount."""
    args = tyro.cli(UmountArgs)
    asyncio.run(run_umount(args.workspace, args.log_level))
