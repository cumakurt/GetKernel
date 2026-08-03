"""Package depot listing and selection helpers."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.package_builder import BUILD_INFO_FILENAME
from utils.validator import path_is_within, validate_build_id

ARCHIVE_DIRNAME = "archive"


def _read_build_info(directory: Path) -> Dict[str, Any]:
    info_path = directory / BUILD_INFO_FILENAME
    if not info_path.is_file():
        return {}
    try:
        data = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _package_paths(directory: Path) -> List[Path]:
    """Prefer the manifest-backed build set and reject names escaping the depot."""
    info = _read_build_info(directory)
    names = info.get("deb_names")
    if isinstance(names, list) and names:
        packages: List[Path] = []
        for name in names:
            if not isinstance(name, str) or Path(name).name != name or not name.endswith(".deb"):
                continue
            path = directory / name
            if path.is_file():
                packages.append(path)
        return sorted(packages)
    return sorted(directory.glob("linux-*.deb"))


def read_build_info(
    packages_dir: Path,
    *,
    build_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Read metadata for the exact latest/archive package set being operated on."""
    if build_id:
        if not validate_build_id(build_id):
            return {}
        directory = packages_dir / ARCHIVE_DIRNAME / f"build-{build_id}"
    else:
        directory = packages_dir / "latest"
    return _read_build_info(directory)


def list_latest_packages(packages_dir: Path) -> List[Dict[str, Any]]:
    latest = packages_dir / "latest"
    if not latest.is_dir():
        return []
    rows: List[Dict[str, Any]] = []
    info = _read_build_info(latest)
    for deb in _package_paths(latest):
        rows.append(
            {
                "name": deb.name,
                "path": str(deb),
                "size_bytes": deb.stat().st_size,
                "set": "latest",
                "requested_version": info.get("requested_version", ""),
                "built_at": info.get("built_at", ""),
            }
        )
    return rows


def list_archived_builds(packages_dir: Path) -> List[Dict[str, Any]]:
    archive = packages_dir / ARCHIVE_DIRNAME
    if not archive.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for d in sorted(archive.iterdir(), reverse=True):
        if not d.is_dir() or not d.name.startswith("build-"):
            continue
        info = _read_build_info(d)
        deb_count = len(_package_paths(d))
        out.append(
            {
                "build_id": d.name.replace("build-", "", 1),
                "directory": str(d),
                "deb_count": deb_count,
                "requested_version": info.get("requested_version", ""),
                "built_at": info.get("built_at", ""),
            }
        )
    return out


def resolve_package_paths(
    packages_dir: Path,
    *,
    build_id: Optional[str] = None,
) -> List[Path]:
    if build_id:
        if not validate_build_id(build_id):
            return []
        archive_root = (packages_dir / ARCHIVE_DIRNAME).resolve()
        target = (archive_root / f"build-{build_id}").resolve()
        if not target.is_dir() or not path_is_within(target, archive_root):
            return []
        return _package_paths(target)
    latest = packages_dir / "latest"
    if not latest.is_dir():
        return []
    return _package_paths(latest)


def archive_latest_to_build_id(packages_dir: Path, build_id: str) -> Optional[Path]:
    if not validate_build_id(build_id):
        return None
    latest = packages_dir / "latest"
    if not latest.is_dir():
        return None
    debs = _package_paths(latest)
    if not debs:
        return None
    dest = packages_dir / ARCHIVE_DIRNAME / f"build-{build_id}"
    dest.mkdir(parents=True, exist_ok=True)
    for p in debs:
        shutil.copy2(p, dest / p.name)
    for meta_name in (BUILD_INFO_FILENAME, "packages.manifest", "checksums.sha256"):
        src = latest / meta_name
        if src.is_file():
            shutil.copy2(src, dest / meta_name)
    return dest


def write_build_history_entry(packages_dir: Path, build_id: str, meta: Dict[str, Any]) -> None:
    hist = packages_dir / "build-history.jsonl"
    hist.parent.mkdir(parents=True, exist_ok=True)
    row = dict(meta)
    row.setdefault("build_id", build_id)
    row.setdefault("recorded_at", datetime.now(timezone.utc).isoformat())
    with open(hist, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def read_build_history(packages_dir: Path, limit: int = 20) -> List[Dict[str, Any]]:
    hist = packages_dir / "build-history.jsonl"
    if not hist.is_file():
        return []
    lines = hist.read_text(encoding="utf-8").splitlines()
    out: List[Dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(out))
