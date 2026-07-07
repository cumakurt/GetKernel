"""Remove GetKernel from the system install location."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import List, Tuple

from utils.constants import GETKERNEL_INSTALL_DIR, GETKERNEL_INSTALL_MARKER
from utils.helpers import is_root, sudo_prefix


MARKER_BEGIN = "# >>> GetKernel PATH (added by install.sh)"
MARKER_END = "# <<< GetKernel PATH"


def _is_getkernel_symlink(path: Path) -> bool:
    if not path.is_symlink():
        return False
    try:
        target = os.readlink(path)
    except OSError:
        return False
    needles = ("getkernel", "GetKernel", ".venv/bin/getkernel")
    return any(n in target for n in needles)


def detect_remnants() -> Tuple[List[Path], List[Path]]:
    paths: List[Path] = []
    rc_files: List[Path] = []
    install_dir = GETKERNEL_INSTALL_DIR
    if install_dir.exists():
        paths.append(install_dir)
    for candidate in (Path("/usr/local/bin/getkernel"), Path("/usr/bin/getkernel")):
        if candidate.exists() and (candidate.is_symlink() or candidate.is_file()):
            if candidate.is_symlink() and not _is_getkernel_symlink(candidate):
                continue
            paths.append(candidate)
    home_roots = [Path("/root")]
    homes = Path("/home")
    if homes.is_dir():
        home_roots.extend(p for p in homes.iterdir() if p.is_dir())
    for home in home_roots:
        try:
            if not home.is_dir():
                continue
        except OSError:
            continue
        local_bin = home / ".local" / "bin" / "getkernel"
        try:
            if local_bin.exists() and _is_getkernel_symlink(local_bin):
                paths.append(local_bin)
        except OSError:
            pass
        for rc_name in (".profile", ".bashrc", ".zshrc"):
            rc = home / rc_name
            try:
                if rc.is_file() and MARKER_BEGIN in rc.read_text(encoding="utf-8", errors="replace"):
                    rc_files.append(rc)
            except OSError:
                continue
    uniq_paths: List[Path] = []
    seen = set()
    for p in paths:
        key = str(p.resolve()) if p.exists() else str(p)
        if key not in seen:
            seen.add(key)
            uniq_paths.append(p)
    return uniq_paths, rc_files


def _strip_path_snippet(rc_file: Path) -> None:
    text = rc_file.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(
        rf"^{re.escape(MARKER_BEGIN)}.*?^{re.escape(MARKER_END)}\n?",
        re.MULTILINE | re.DOTALL,
    )
    new_text = pattern.sub("", text)
    if new_text != text:
        rc_file.write_text(new_text, encoding="utf-8")


def uninstall_getkernel(*, assume_yes: bool = False) -> List[str]:
    """Remove install tree, symlinks, and PATH snippets. Returns removed paths."""
    if not is_root() and not sudo_prefix():
        raise PermissionError("Root or sudo required to uninstall GetKernel.")

    paths, rc_files = detect_remnants()
    if not paths and not rc_files:
        return []

    removed: List[str] = []
    for p in paths:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=False)
        elif p.is_symlink() or p.is_file():
            p.unlink(missing_ok=True)
        removed.append(str(p))
    for rc in rc_files:
        _strip_path_snippet(rc)
        removed.append(str(rc))
    marker = GETKERNEL_INSTALL_DIR / GETKERNEL_INSTALL_MARKER
    if marker.is_file():
        marker.unlink(missing_ok=True)
    return removed
