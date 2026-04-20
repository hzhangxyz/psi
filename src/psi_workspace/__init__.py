"""Psi Workspace - SquashFS/OverlayFS manager."""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from loguru import logger


class WorkspaceManager:
    """Manages SquashFS and OverlayFS for workspace snapshots."""

    def __init__(self) -> None:
        self.manifest_file = "manifest.json"
        logger.debug("WorkspaceManager initialized")

    def mount(self, squashfs_path: str, output_dir: str) -> None:
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
        subprocess.run(
            ["mount", "-t", "squashfs", str(squashfs), str(lower_dir)],
            check=True,
        )
        logger.info(f"SquashFS mounted | path={lower_dir}")

        # Mount overlayfs
        logger.debug(f"Mounting overlayfs | lower={lower_dir} | upper={upper_dir} | work={work_dir}")
        subprocess.run(
            [
                "mount", "-t", "overlay", "overlay",
                "-o", f"lowerdir={lower_dir},upperdir={upper_dir},workdir={work_dir}",
                str(workspace),
            ],
            check=True,
        )
        logger.info(f"OverlayFS mounted | path={workspace}")

        # Create/update manifest
        manifest_path = workspace.parent / self.manifest_file
        self._update_manifest(manifest_path, squashfs.name, "mounted")

        logger.info(f"Workspace mounted successfully | squashfs={squashfs_path} | workspace={output_dir}")

    def unmount(self, workspace_dir: str) -> None:
        """Unmount a workspace and clean up."""
        workspace = Path(workspace_dir).resolve()

        logger.info(f"Unmounting workspace | path={workspace}")

        # Unmount overlayfs
        logger.debug(f"Unmounting overlayfs | path={workspace}")
        subprocess.run(["umount", str(workspace)], check=True)
        logger.info(f"OverlayFS unmounted | path={workspace}")

        # Unmount squashfs (lower)
        lower_dir = Path(str(workspace) + ".lower")
        if lower_dir.exists():
            logger.debug(f"Unmounting squashfs | path={lower_dir}")
            subprocess.run(["umount", str(lower_dir)], check=True)
            logger.info(f"SquashFS unmounted | path={lower_dir}")

        logger.info(f"Workspace unmounted successfully | workspace={workspace_dir}")

    def snapshot(
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
            print(f"No changes to snapshot (upper dir not found)", file=sys.stderr)
            return

        # Create squashfs from upper directory
        logger.debug(f"Creating squashfs | source={upper_dir} | output={output}")
        subprocess.run(
            ["mksquashfs", str(upper_dir), str(output), "-noappend"],
            check=True,
        )
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

        manifest = json.loads(manifest_path.read_text())
        snapshots = manifest.get("snapshots", [])

        logger.info(f"Found {len(snapshots)} snapshots")

        print(f"Snapshots for {workspace_dir}:")
        for snap in snapshots:
            print(f"  - {snap['name']}: {snap['description']} ({snap['created_at']})")
            logger.debug(f"Snapshot | name={snap['name']} | created_at={snap['created_at']}")

    def _update_manifest(self, manifest_path: Path, name: str, status: str) -> None:
        """Update manifest file."""
        logger.debug(f"Updating manifest | path={manifest_path} | name={name} | status={status}")

        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
        else:
            manifest = {"current": None, "snapshots": []}

        manifest["current"] = {
            "name": name,
            "status": status,
            "mounted_at": datetime.now().isoformat(),
        }

        manifest_path.write_text(json.dumps(manifest, indent=2))
        logger.debug(f"Manifest updated | current={manifest['current']}")

    def _add_snapshot(self, manifest_path: Path, name: str, description: str) -> None:
        """Add snapshot entry to manifest."""
        logger.debug(f"Adding snapshot to manifest | path={manifest_path} | name={name}")

        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
        else:
            manifest = {"current": None, "snapshots": []}

        snapshot_entry = {
            "name": name,
            "description": description,
            "created_at": datetime.now().isoformat(),
        }

        manifest["snapshots"].append(snapshot_entry)
        manifest_path.write_text(json.dumps(manifest, indent=2))

        logger.debug(f"Snapshot entry added | snapshot={snapshot_entry}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Psi Workspace Manager")
    parser.add_argument("--log-level", default="INFO", help="Log level (DEBUG, INFO, WARNING, ERROR)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Mount command
    mount_parser = subparsers.add_parser("mount", help="Mount squashfs as workspace")
    mount_parser.add_argument("squashfs", help="SquashFS image path")
    mount_parser.add_argument("output", help="Output workspace directory")

    # Unmount command
    unmount_parser = subparsers.add_parser("unmount", help="Unmount workspace")
    unmount_parser.add_argument("workspace", help="Workspace directory")

    # Snapshot command
    snapshot_parser = subparsers.add_parser("snapshot", help="Create snapshot")
    snapshot_parser.add_argument("workspace", help="Workspace directory")
    snapshot_parser.add_argument("output", help="Output squashfs path")
    snapshot_parser.add_argument("--description", default="", help="Snapshot description")

    # List command
    list_parser = subparsers.add_parser("list", help="List snapshots")
    list_parser.add_argument("workspace", help="Workspace directory")

    args = parser.parse_args()

    # Configure logger
    logger.remove()
    logger.add(
        lambda msg: print(msg, end=""),
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>workspace</cyan> | {message}",
        level=args.log_level,
    )

    manager = WorkspaceManager()

    if args.command == "mount":
        manager.mount(args.squashfs, args.output)
    elif args.command == "unmount":
        manager.unmount(args.workspace)
    elif args.command == "snapshot":
        manager.snapshot(args.workspace, args.output, args.description)
    elif args.command == "list":
        manager.list_snapshots(args.workspace)


if __name__ == "__main__":
    main()