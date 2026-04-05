"""Locate and verify DEB packages produced by make deb-pkg."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.helpers import project_root, run_cmd

BUILD_INFO_FILENAME = "build-info.json"


def find_matching_stored_packages(
    output_dir: Path | str,
    requested_version: str,
    localversion: str,
) -> Optional[List[Path]]:
    """
    Return .deb paths under output_dir/latest if they match this kernel version
    (build-info.json preferred, else fuzzy match on linux-image-* names).
    """
    root = Path(output_dir)
    latest = root / "latest"
    if not latest.is_dir():
        return None
    info_path = latest / BUILD_INFO_FILENAME
    if info_path.is_file():
        try:
            data = json.loads(info_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            data = None
        else:
            if (
                isinstance(data, dict)
                and data.get("requested_version") == requested_version
                and data.get("localversion") == localversion
            ):
                debs: List[Path] = []
                for name in data.get("deb_names", []):
                    p = latest / name
                    if p.is_file():
                        debs.append(p)
                if debs:
                    return sorted(debs, key=lambda x: x.name)
    return _fuzzy_match_stored_packages(latest, requested_version, localversion)


def _fuzzy_match_stored_packages(
    latest: Path,
    version: str,
    localversion: str,
) -> Optional[List[Path]]:
    """Fallback when build-info.json is missing (older GetKernel runs)."""
    lv = localversion.lstrip("-").lower() if localversion else ""
    images = sorted(latest.glob("linux-image-*.deb"))
    if not images:
        return None
    v = version.lower().strip()
    v_compact = re.sub(r"[^0-9a-z]", "", v)
    matched_image: Optional[Path] = None
    for p in images:
        n = p.name.lower()
        if lv and lv not in n:
            continue
        n_compact = re.sub(r"[^0-9a-z]", "", n)
        if v in n or (len(v_compact) >= 4 and v_compact in n_compact):
            matched_image = p
            break
    if not matched_image:
        return None
    # Collect all linux-*.deb sharing the same Debian revision segment (after first underscore)
    tag = matched_image.name.split("_", 1)[1] if "_" in matched_image.name else ""
    if not tag:
        return [matched_image]
    out: List[Path] = []
    for p in sorted(latest.glob("linux-*.deb")):
        if p.name == BUILD_INFO_FILENAME:
            continue
        if tag in p.name:
            out.append(p)
    return out if out else None


class PackageBuilder:
    """Collect linux-*.deb from build parent directory."""

    def __init__(self, build_dir: str, output_dir: str | None = None) -> None:
        self.build_dir = Path(build_dir).resolve()
        root = project_root()
        self.output_dir = Path(output_dir) if output_dir else root / "data" / "packages"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def find_built_packages(self) -> List[Path]:
        parent = self.build_dir.parent
        debs: List[Path] = []
        for folder in (parent, self.build_dir):
            if folder.is_dir():
                debs.extend(sorted(folder.glob("linux-*.deb")))
        # dedupe
        seen = set()
        uniq: List[Path] = []
        for p in debs:
            if p.name in seen:
                continue
            seen.add(p.name)
            uniq.append(p)
        return uniq

    def verify_packages(self, package_list: List[Path]) -> Tuple[bool, List[str]]:
        errors: List[str] = []
        for deb in package_list:
            if deb.stat().st_size <= 0:
                errors.append(f"empty file: {deb}")
                continue
            cp = run_cmd(["dpkg-deb", "-I", str(deb)])
            if cp.returncode != 0:
                errors.append(f"invalid deb: {deb}")
        return len(errors) == 0, errors

    def get_package_info(self, deb_file: Path) -> Dict[str, str]:
        cp = run_cmd(["dpkg-deb", "-I", str(deb_file)])
        info: Dict[str, str] = {"file": str(deb_file)}
        if cp.returncode != 0:
            return info
        for line in cp.stdout.splitlines():
            if line.startswith(" "):
                continue
            if ":" in line:
                k, _, v = line.partition(":")
                info[k.strip().lower()] = v.strip()
        return info

    def create_package_metadata(self, version: str, arch: str = "amd64") -> Dict[str, str]:
        return {"version": version, "architecture": arch}

    def move_packages(
        self,
        packages: List[Path],
        create_manifest: bool = True,
        *,
        requested_version: Optional[str] = None,
        localversion: Optional[str] = None,
    ) -> List[Path]:
        sub = self.output_dir / "latest"
        sub.mkdir(parents=True, exist_ok=True)
        out_paths: List[Path] = []
        for p in packages:
            dest = sub / p.name
            shutil.copy2(p, dest)
            out_paths.append(dest)
        if create_manifest and out_paths:
            self.create_manifest(out_paths)
            self.calculate_checksums(out_paths)
        if requested_version is not None and localversion is not None and out_paths:
            self._write_build_info(requested_version, localversion, out_paths)
        return out_paths

    def _write_build_info(
        self,
        requested_version: str,
        localversion: str,
        packages: List[Path],
    ) -> None:
        data = {
            "requested_version": requested_version,
            "localversion": localversion,
            "deb_names": [p.name for p in packages],
            "built_at": datetime.now(timezone.utc).isoformat(),
        }
        dest = self.output_dir / "latest" / BUILD_INFO_FILENAME
        dest.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def create_manifest(self, packages: List[Path]) -> Path:
        manifest = self.output_dir / "latest" / "packages.manifest"
        lines = []
        for p in packages:
            sha = hashlib.sha256(p.read_bytes()).hexdigest()
            lines.append(f"{p.name}\t{p.stat().st_size}\tsha256:{sha}\n")
        manifest.write_text("".join(lines), encoding="utf-8")
        return manifest

    def calculate_checksums(self, packages: List[Path]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        chk = self.output_dir / "latest" / "checksums.sha256"
        parts = []
        for p in packages:
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            out[p.name] = h
            parts.append(f"{h}  {p.name}\n")
        chk.write_text("".join(parts), encoding="utf-8")
        return out

    def compress_packages(self, packages: List[Path], output_archive: str | None = None) -> Path:
        import tarfile

        name = output_archive or str(self.output_dir / "packages.tar.xz")
        out = Path(name)
        with tarfile.open(out, "w:xz") as tf:
            for p in packages:
                tf.add(p, arcname=p.name)
        return out

    def cleanup_build_artifacts(
        self, keep_packages: bool = True, dry_run: bool = False
    ) -> int:
        """Remove intermediate build files. Return count of removed (or would-remove) items.

        When *keep_packages* is True the collected .deb files under
        ``output_dir`` are preserved.

        When *dry_run* is True, nothing is deleted; the return value is how many
        files would be removed.
        """
        removed = 0
        # Clean the kernel source tree (make mrproper artefacts)
        for pattern in ("*.o", "*.ko", "*.cmd", "*.mod", "*.mod.c"):
            for p in self.build_dir.rglob(pattern):
                if dry_run:
                    removed += 1
                    continue
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
        # Optionally remove built debs from the build parent (originals)
        if not keep_packages:
            parent = self.build_dir.parent
            for deb in parent.glob("linux-*.deb"):
                if dry_run:
                    removed += 1
                    continue
                try:
                    deb.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed
