"""Validation helpers."""

from __future__ import annotations

import re
import shutil
import tarfile
from pathlib import Path
from typing import Tuple

from utils.exceptions import SecurityError

BACKUP_ID_RE = re.compile(r"^backup-\d{8}-\d{6}$")
BUILD_ID_RE = re.compile(r"^[a-f0-9]{12}$")
BOOT_BACKUP_FILE_RE = re.compile(
    r"^(vmlinuz|initrd\.img|System\.map|config)-[^/\\]+$"
)
KERNEL_VERSION_RE = re.compile(
    r"^[0-9]+\.[0-9]+(\.[0-9]+)?(-rc[0-9]+|-beta[0-9]+)?$"
)


def validate_kernel_version(version: str) -> bool:
    if not version or len(version) > 64:
        return False
    return bool(KERNEL_VERSION_RE.match(version.strip()))


def validate_backup_id(backup_id: str) -> bool:
    return bool(BACKUP_ID_RE.match(backup_id.strip()))


def validate_build_id(build_id: str) -> bool:
    return bool(BUILD_ID_RE.match(build_id.strip()))


def validate_boot_backup_filename(name: str) -> bool:
    if not name or "/" in name or "\\" in name or ".." in name:
        return False
    return bool(BOOT_BACKUP_FILE_RE.match(name))


def path_is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def safe_extract_path(target_dir: Path, member_name: str) -> Path:
    """Resolve tarball member path; reject path traversal."""
    dest = (target_dir / member_name).resolve()
    try:
        dest.relative_to(target_dir.resolve())
    except ValueError as exc:
        raise SecurityError(f"Unsafe archive member: {member_name!r}") from exc
    return dest


def safe_extract_tarball(tf: tarfile.TarFile, target_dir: Path) -> None:
    """Extract only regular files and directories; reject symlinks and hard links."""
    root = target_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    for member in tf.getmembers():
        safe_extract_path(root, member.name)
        if member.isdir():
            (root / member.name).mkdir(parents=True, exist_ok=True)
            continue
        if member.isreg():
            dest = safe_extract_path(root, member.name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            extracted = tf.extractfile(member)
            if extracted is None:
                raise SecurityError(f"Cannot extract archive member: {member.name!r}")
            with extracted as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)
            continue
        raise SecurityError(f"Unsafe archive member type: {member.name!r}")


def check_file_safety(filepath: Path, max_bytes: int = 600 * 1024 * 1024) -> Tuple[bool, str]:
    path = filepath.resolve()
    if ".." in filepath.parts:
        return False, "path contains parent segments"
    if filepath.is_symlink():
        return False, "symlink not allowed"
    if not path.is_file():
        return False, "not a regular file"
    if path.stat().st_size > max_bytes:
        return False, "file too large"
    if not path.name.endswith(".deb"):
        return False, "not a .deb file"
    return True, "ok"
