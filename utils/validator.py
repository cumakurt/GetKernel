"""Validation helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Tuple

from utils.exceptions import SecurityError


def validate_kernel_version(version: str) -> bool:
    if not version or len(version) > 64:
        return False
    return bool(re.match(r"^[0-9]+\.[0-9]+(\.[0-9]+)?(-rc[0-9]+)?$", version.strip()))


def safe_extract_path(target_dir: Path, member_name: str) -> Path:
    """Resolve tarball member path; reject path traversal."""
    dest = (target_dir / member_name).resolve()
    try:
        dest.relative_to(target_dir.resolve())
    except ValueError as exc:
        raise SecurityError(f"Unsafe archive member: {member_name!r}") from exc
    return dest


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
    return True, "ok"
