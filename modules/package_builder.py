"""Locate and verify DEB packages produced by make deb-pkg."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.helpers import project_root, run_cmd
from utils.validator import canonical_kernel_release

BUILD_INFO_FILENAME = "build-info.json"


def _is_kernel_image_package(package: str, release: str) -> bool:
    return package in (
        f"linux-image-{release}",
        f"linux-image-unsigned-{release}",
    )


def _is_kernel_debug_package(package: str, release: str) -> bool:
    return package in (
        f"linux-image-{release}-dbg",
        f"linux-image-unsigned-{release}-dbg",
    )


def _is_kernel_headers_package(package: str, release: str) -> bool:
    return package == f"linux-headers-{release}"


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
                    if not isinstance(name, str) or Path(name).name != name:
                        continue
                    p = latest / name
                    if p.is_file() and p.suffix == ".deb":
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
    images = sorted(latest.glob("linux-image-*.deb"))
    if not images:
        return None
    try:
        expected_release = canonical_kernel_release(version, localversion).lower()
    except ValueError:
        return None
    matched_image: Optional[Path] = None
    for p in images:
        n = p.name.lower()
        package_name = n.split("_", 1)[0]
        release = package_name.removeprefix("linux-image-")
        if release == expected_release:
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

    def find_built_packages(
        self,
        *,
        expected_kernel_release: Optional[str] = None,
        modified_after: Optional[float] = None,
    ) -> List[Path]:
        """Find this build's packages without mixing in stale sibling outputs."""
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
            if modified_after is not None:
                try:
                    # Some filesystems expose mtimes at one-second granularity.
                    if p.stat().st_mtime < modified_after - 1.0:
                        continue
                except OSError:
                    continue
            seen.add(p.name)
            uniq.append(p)
        if expected_kernel_release:
            image_found = False
            for p in uniq:
                info = self.get_package_info(p)
                package = info.get("package", p.name.split("_", 1)[0])
                if _is_kernel_image_package(package, expected_kernel_release):
                    image_found = True
                    break
            if not image_found:
                return []
        return uniq

    def verify_packages(
        self,
        package_list: List[Path],
        *,
        expected_kernel_release: Optional[str] = None,
        require_headers: bool = False,
    ) -> Tuple[bool, List[str]]:
        errors: List[str] = []
        package_names: List[str] = []
        for deb in package_list:
            try:
                size = deb.stat().st_size
            except OSError as exc:
                errors.append(f"cannot read {deb}: {exc}")
                continue
            if size <= 0:
                errors.append(f"empty file: {deb}")
                continue
            cp = run_cmd(["dpkg-deb", "-I", str(deb)])
            if cp.returncode != 0:
                errors.append(f"invalid deb: {deb}")
                continue
            info = self.get_package_info(deb)
            package = info.get("package", "")
            if not package:
                errors.append(f"missing Package metadata: {deb}")
                continue
            package_names.append(package)

        if expected_kernel_release:
            images = [
                name
                for name in package_names
                if _is_kernel_image_package(name, expected_kernel_release)
            ]
            mismatched = [
                name
                for name in package_names
                if (name.startswith("linux-image-") or name.startswith("linux-headers-"))
                and not _is_kernel_image_package(name, expected_kernel_release)
                and not _is_kernel_debug_package(name, expected_kernel_release)
                and not _is_kernel_headers_package(name, expected_kernel_release)
            ]
            if not images:
                errors.append(f"missing kernel image package for {expected_kernel_release}")
            if mismatched:
                errors.append("packages from another kernel release: " + ", ".join(mismatched))
            if require_headers and not any(
                _is_kernel_headers_package(name, expected_kernel_release)
                for name in package_names
            ):
                errors.append(
                    f"missing linux-headers package for {expected_kernel_release}; "
                    "VMware/DKMS modules would not be buildable"
                )
        return len(errors) == 0, errors

    def get_package_info(self, deb_file: Path) -> Dict[str, str]:
        cp = run_cmd(["dpkg-deb", "-f", str(deb_file)])
        info: Dict[str, str] = {"file": str(deb_file)}
        if cp.returncode != 0:
            return info
        current_key = ""
        for line in cp.stdout.splitlines():
            if line[:1].isspace() and current_key:
                info[current_key] = f"{info[current_key]} {line.strip()}".strip()
                continue
            if ":" in line:
                k, _, v = line.partition(":")
                current_key = k.strip().lower()
                info[current_key] = v.strip()
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
        kernel_release: Optional[str] = None,
        build_id: Optional[str] = None,
    ) -> List[Path]:
        sub = self.output_dir / "latest"
        sub.mkdir(parents=True, exist_ok=True)
        # ``latest`` is one coherent build set. Old files must not leak into a
        # new install or archive when package names differ between releases.
        for old in sub.glob("linux-*.deb"):
            old.unlink()
        for metadata_name in (BUILD_INFO_FILENAME, "packages.manifest", "checksums.sha256"):
            old = sub / metadata_name
            if old.is_file():
                old.unlink()
        out_paths: List[Path] = []
        for p in packages:
            dest = sub / p.name
            shutil.copy2(p, dest)
            out_paths.append(dest)
        if create_manifest and out_paths:
            checksums = self.calculate_checksums(out_paths)
            self.create_manifest(out_paths, checksums=checksums)
        if requested_version is not None and localversion is not None and out_paths:
            self._write_build_info(
                requested_version,
                localversion,
                out_paths,
                kernel_release=kernel_release,
                build_id=build_id,
            )
        if build_id and out_paths:
            self._archive_build(build_id, out_paths)
        return out_paths

    def _archive_build(self, build_id: str, packages: List[Path]) -> Path:
        from modules.package_depot import archive_latest_to_build_id

        archived = archive_latest_to_build_id(self.output_dir, build_id)
        if archived is None:
            dest = self.output_dir / "archive" / f"build-{build_id}"
            dest.mkdir(parents=True, exist_ok=True)
            for p in packages:
                shutil.copy2(p, dest / p.name)
            info = self.output_dir / "latest" / BUILD_INFO_FILENAME
            if info.is_file():
                shutil.copy2(info, dest / BUILD_INFO_FILENAME)
            return dest
        return archived

    def _write_build_info(
        self,
        requested_version: str,
        localversion: str,
        packages: List[Path],
        *,
        kernel_release: Optional[str] = None,
        build_id: Optional[str] = None,
    ) -> None:
        data = {
            "requested_version": requested_version,
            "localversion": localversion,
            "deb_names": [p.name for p in packages],
            "built_at": datetime.now(timezone.utc).isoformat(),
        }
        if kernel_release:
            data["kernel_release"] = kernel_release
        if build_id:
            data["build_id"] = build_id
        dest = self.output_dir / "latest" / BUILD_INFO_FILENAME
        dest.write_text(json.dumps(data, indent=2), encoding="utf-8")
        if build_id:
            from modules.package_depot import write_build_history_entry

            write_build_history_entry(
                self.output_dir,
                build_id,
                {
                    "requested_version": requested_version,
                    "localversion": localversion,
                    "kernel_release": kernel_release or "",
                    "deb_count": len(packages),
                },
            )

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def create_manifest(
        self,
        packages: List[Path],
        *,
        checksums: Optional[Dict[str, str]] = None,
    ) -> Path:
        manifest = self.output_dir / "latest" / "packages.manifest"
        lines = []
        for p in packages:
            sha = (checksums or {}).get(p.name) or self._sha256_file(p)
            lines.append(f"{p.name}\t{p.stat().st_size}\tsha256:{sha}\n")
        manifest.write_text("".join(lines), encoding="utf-8")
        return manifest

    def calculate_checksums(self, packages: List[Path]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        chk = self.output_dir / "latest" / "checksums.sha256"
        parts = []
        for p in packages:
            h = self._sha256_file(p)
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
