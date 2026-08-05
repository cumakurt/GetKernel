"""Locate and verify DEB packages produced by make deb-pkg."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.constants import EXTERNAL_MODULE_HEADER_FILES
from utils.helpers import project_root, run_cmd
from utils.validator import canonical_kernel_release, validate_kernel_release

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
        prefix = "linux-image-"
        release = package_name[len(prefix) :] if package_name.startswith(prefix) else package_name
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
        package_paths: Dict[str, Path] = {}
        errors.extend(self._verify_depot_checksums(package_list))
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
            package_paths[package] = deb

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
            if require_headers:
                header_name = f"linux-headers-{expected_kernel_release}"
                header_deb = package_paths.get(header_name)
                if header_deb is None:
                    errors.append(
                        f"missing linux-headers package for {expected_kernel_release}; "
                        "VMware/DKMS modules would not be buildable"
                    )
                else:
                    members = self._regular_deb_members(header_deb)
                    if members is None:
                        errors.append(f"cannot inspect external-module files in {header_deb}")
                    else:
                        prefix = f"usr/src/linux-headers-{expected_kernel_release}/"
                        missing = [
                            relative
                            for relative in EXTERNAL_MODULE_HEADER_FILES
                            if f"{prefix}{relative}" not in members
                        ]
                        if missing:
                            errors.append(
                                f"incomplete linux-headers package for "
                                f"{expected_kernel_release}: missing "
                                + ", ".join(missing)
                                + "; VMware/DKMS modules would not be buildable"
                            )
        return len(errors) == 0, errors

    @staticmethod
    def _regular_deb_members(deb_file: Path) -> Optional[set[str]]:
        """Return regular data-archive members reported by dpkg-deb."""
        cp = run_cmd(["dpkg-deb", "-c", str(deb_file)], timeout=300)
        if cp.returncode != 0:
            return None
        members: set[str] = set()
        for line in cp.stdout.splitlines():
            if not line.startswith("-"):
                continue
            marker = line.find(" ./")
            if marker < 0:
                continue
            member = line[marker + 3 :].strip()
            if member:
                members.add(member)
        return members

    def embed_kernel_config_in_headers(
        self,
        package_list: List[Path],
        *,
        expected_kernel_release: str,
        config_file: Path | str,
    ) -> Path:
        """Atomically add the build .config to the matching headers DEB.

        Recent upstream ``install-extmod-build`` scripts package auto.conf but
        omit ``.config``.  That is sufficient for Kbuild, while VMware's
        proprietary header validator rejects the same otherwise-complete tree.
        Repacking here keeps the installed workaround owned by the headers
        package and ensures it survives installation on a newly booted kernel.
        """
        if not validate_kernel_release(expected_kernel_release):
            raise ValueError(f"Invalid kernel release: {expected_kernel_release!r}")

        source_config = Path(config_file).resolve()
        if not source_config.is_file():
            raise ValueError(f"Kernel build configuration is missing: {source_config}")
        try:
            config_size = source_config.stat().st_size
        except OSError as exc:
            raise OSError(f"Cannot read kernel build configuration: {exc}") from exc
        if config_size <= 0:
            raise ValueError(f"Kernel build configuration is empty: {source_config}")

        expected_package = f"linux-headers-{expected_kernel_release}"
        matches: List[Path] = []
        for package in package_list:
            info = self.get_package_info(package)
            if info.get("package") == expected_package:
                matches.append(package)
        if len(matches) != 1:
            raise ValueError(
                f"Expected exactly one {expected_package} package, found {len(matches)}"
            )

        header_deb = matches[0]
        if header_deb.is_symlink() or not header_deb.is_file() or header_deb.suffix != ".deb":
            raise ValueError(f"Invalid headers package path: {header_deb}")

        prefix = Path("usr") / "src" / expected_package
        with tempfile.TemporaryDirectory(
            prefix=".headers-repack-",
            dir=header_deb.parent,
        ) as temp_name:
            temp_dir = Path(temp_name)
            package_root = temp_dir / "root"
            package_root.mkdir()
            extracted = run_cmd(
                ["dpkg-deb", "-R", str(header_deb), str(package_root)],
                timeout=900,
            )
            if extracted.returncode != 0:
                detail = (extracted.stderr or extracted.stdout or "unknown error")[-2000:]
                raise OSError(f"Cannot extract {header_deb.name}: {detail}")

            header_root = package_root / prefix
            if header_root.is_symlink() or not header_root.is_dir():
                raise ValueError(
                    f"Headers package does not contain the expected directory: /{prefix}"
                )
            if not (header_root / "Makefile").is_file():
                raise ValueError(f"Headers package is incomplete under /{prefix}")

            packaged_config = header_root / ".config"
            if packaged_config.is_symlink() or (
                packaged_config.exists() and not packaged_config.is_file()
            ):
                raise ValueError(f"Unsafe .config entry in {header_deb.name}")
            old_size = packaged_config.stat().st_size if packaged_config.is_file() else 0
            shutil.copy2(source_config, packaged_config)
            os.chmod(packaged_config, 0o644)

            member_name = (prefix / ".config").as_posix()
            self._update_debian_md5sums(package_root, member_name, packaged_config)
            self._adjust_installed_size(package_root, old_size, config_size)

            rebuilt = temp_dir / header_deb.name
            built = run_cmd(
                ["dpkg-deb", "--build", str(package_root), str(rebuilt)],
                timeout=900,
            )
            if built.returncode != 0 or not rebuilt.is_file():
                detail = (built.stderr or built.stdout or "unknown error")[-2000:]
                raise OSError(f"Cannot rebuild {header_deb.name}: {detail}")
            members = self._regular_deb_members(rebuilt)
            if members is None or member_name not in members:
                raise OSError(
                    f"Rebuilt {header_deb.name} does not contain /{member_name}"
                )
            rebuilt.replace(header_deb)

        return header_deb

    @staticmethod
    def _update_debian_md5sums(
        package_root: Path,
        member_name: str,
        source: Path,
    ) -> None:
        """Keep DEBIAN/md5sums consistent when the generated package has one."""
        md5sums = package_root / "DEBIAN" / "md5sums"
        if not md5sums.is_file():
            return
        lines = md5sums.read_text(encoding="utf-8", errors="strict").splitlines()
        kept: List[str] = []
        for line in lines:
            fields = line.split(maxsplit=1)
            if len(fields) == 2 and fields[1].lstrip("*") == member_name:
                continue
            kept.append(line)
        # MD5 is required by Debian package metadata; depot integrity uses SHA-256.
        digest = hashlib.md5(source.read_bytes()).hexdigest()  # noqa: S324
        kept.append(f"{digest}  {member_name}")
        md5sums.write_text("\n".join(kept) + "\n", encoding="utf-8")

    @staticmethod
    def _adjust_installed_size(package_root: Path, old_size: int, new_size: int) -> None:
        """Account for the embedded config in the package's Installed-Size."""
        control = package_root / "DEBIAN" / "control"
        if not control.is_file():
            return
        lines = control.read_text(encoding="utf-8", errors="strict").splitlines()
        old_kib = (old_size + 1023) // 1024
        new_kib = (new_size + 1023) // 1024
        for index, line in enumerate(lines):
            if not line.startswith("Installed-Size:"):
                continue
            raw = line.partition(":")[2].strip()
            try:
                installed_kib = int(raw)
            except ValueError:
                return
            lines[index] = f"Installed-Size: {max(0, installed_kib - old_kib + new_kib)}"
            control.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return

    @classmethod
    def _verify_depot_checksums(cls, package_list: List[Path]) -> List[str]:
        """Verify checksums when packages come from a GetKernel depot set."""
        if not package_list:
            return []
        parents = {package.resolve().parent for package in package_list}
        if len(parents) != 1:
            return []
        checksum_file = next(iter(parents)) / "checksums.sha256"
        if not checksum_file.is_file():
            return []  # Backward compatibility with builds created before manifests.
        try:
            lines = checksum_file.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            return [f"cannot read {checksum_file}: {exc}"]
        expected: Dict[str, str] = {}
        for line in lines:
            parts = line.split()
            if len(parts) != 2:
                return [f"invalid checksum manifest line: {line!r}"]
            digest, name = parts
            name = name.lstrip("*")
            if (
                Path(name).name != name
                or len(digest) != 64
                or any(c not in "0123456789abcdefABCDEF" for c in digest)
            ):
                return [f"invalid checksum manifest entry: {line!r}"]
            if name in expected:
                return [f"duplicate checksum manifest entry: {name}"]
            expected[name] = digest.lower()
        errors: List[str] = []
        for package in package_list:
            digest = expected.get(package.name)
            if digest is None:
                errors.append(f"missing checksum entry: {package.name}")
                continue
            try:
                actual = cls._sha256_file(package)
            except OSError as exc:
                errors.append(f"cannot checksum {package.name}: {exc}")
                continue
            if not hmac.compare_digest(actual, digest):
                errors.append(f"checksum mismatch: {package.name}")
        # A truncated depot set could otherwise disappear from package_list
        # before verification. Check that every package recorded at publish
        # time is still physically present, including optional/debug artifacts.
        parent = next(iter(parents))
        for name in expected:
            if name.endswith(".deb") and not (parent / name).is_file():
                errors.append(f"missing depot package: {name}")
        return errors

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
        if not packages:
            return []
        names = [p.name for p in packages]
        if len(set(names)) != len(names):
            raise ValueError("Package list contains duplicate file names")
        for package in packages:
            if not package.is_file() or package.suffix != ".deb":
                raise ValueError(f"Invalid package path: {package}")

        latest = self.output_dir / "latest"
        stage = Path(tempfile.mkdtemp(prefix=".latest-stage-", dir=self.output_dir))
        previous = self.output_dir / f".latest-previous-{uuid.uuid4().hex}"
        staged_paths: List[Path] = []
        previous_moved = False
        try:
            for package in packages:
                dest = stage / package.name
                shutil.copy2(package, dest)
                staged_paths.append(dest)
            if create_manifest:
                checksums = self.calculate_checksums(staged_paths, directory=stage)
                self.create_manifest(staged_paths, checksums=checksums, directory=stage)
            if requested_version is not None and localversion is not None:
                self._write_build_info(
                    requested_version,
                    localversion,
                    staged_paths,
                    kernel_release=kernel_release,
                    build_id=build_id,
                    directory=stage,
                )

            # Publish only after every package and metadata file is complete.
            # A failed copy therefore leaves the previous latest set untouched.
            if latest.exists():
                latest.replace(previous)
                previous_moved = True
            stage.replace(latest)
        except BaseException:
            if previous_moved and not latest.exists() and previous.exists():
                previous.replace(latest)
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
            raise
        else:
            if previous.exists():
                shutil.rmtree(previous, ignore_errors=True)

        out_paths = [latest / name for name in names]
        if build_id:
            self._archive_build(build_id, out_paths)
            from modules.package_depot import write_build_history_entry

            write_build_history_entry(
                self.output_dir,
                build_id,
                {
                    "requested_version": requested_version or "",
                    "localversion": localversion or "",
                    "kernel_release": kernel_release or "",
                    "deb_count": len(out_paths),
                },
            )
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
        directory: Optional[Path] = None,
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
        dest = (directory or self.output_dir / "latest") / BUILD_INFO_FILENAME
        dest.write_text(json.dumps(data, indent=2), encoding="utf-8")

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
        directory: Optional[Path] = None,
    ) -> Path:
        manifest = (directory or self.output_dir / "latest") / "packages.manifest"
        lines = []
        for p in packages:
            sha = (checksums or {}).get(p.name) or self._sha256_file(p)
            lines.append(f"{p.name}\t{p.stat().st_size}\tsha256:{sha}\n")
        manifest.write_text("".join(lines), encoding="utf-8")
        return manifest

    def calculate_checksums(
        self,
        packages: List[Path],
        *,
        directory: Optional[Path] = None,
    ) -> Dict[str, str]:
        out: Dict[str, str] = {}
        chk = (directory or self.output_dir / "latest") / "checksums.sha256"
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
        artifact_suffixes = (".o", ".ko", ".cmd", ".mod", ".mod.c")
        artifact_names = {"vmlinux", "System.map", "Module.symvers", "modules.order"}
        artifacts: List[Path] = []
        for path in self.build_dir.rglob("*"):
            if path.is_file() and (
                path.name in artifact_names
                or path.name.endswith(artifact_suffixes)
            ):
                artifacts.append(path)
        removed = len(artifacts) if dry_run else 0
        if not dry_run:
            # Kbuild knows all architecture-specific generated files. This is
            # both more complete and faster than walking the tree once per glob.
            clean = run_cmd(["make", "clean"], cwd=self.build_dir, timeout=3600)
            if clean.returncode == 0:
                removed = len(artifacts)
            else:
                for path in artifacts:
                    try:
                        path.unlink()
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
