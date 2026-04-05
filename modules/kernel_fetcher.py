"""Fetch kernel versions and sources from kernel.org."""

from __future__ import annotations

import hashlib
import json
import sys
import tarfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from utils.constants import (
    APP_VERSION,
    CDN_MIRRORS,
    DEVELOPER_GITHUB_REPO_URL,
    KERNEL_ORG_RELEASES_JSON,
)
from utils.exceptions import DownloadError, VerificationError
from utils.helpers import cdn_source_url, kernel_major_branch, project_root
from utils.validator import safe_extract_path


class KernelFetcher:
    """Download metadata and kernel tarballs from kernel.org."""

    def __init__(self, cache_dir: Optional[str] = None):
        root = project_root()
        self.cache_dir = Path(cache_dir) if cache_dir else root / "data" / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.base_url = "https://www.kernel.org"
        self.cdn_url = CDN_MIRRORS[0]
        self.releases_api = KERNEL_ORG_RELEASES_JSON
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    f"GetKernel/{APP_VERSION} (+{DEVELOPER_GITHUB_REPO_URL})"
                )
            }
        )
        self._download_progress = 0.0
        self._releases_cache: Optional[Dict[str, Any]] = None

    def fetch_kernel_versions(
        self,
        include_beta: bool = True,
        include_rc: bool = True,
    ) -> Dict[str, Any]:
        try:
            r = self.session.get(self.releases_api, timeout=60)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, json.JSONDecodeError) as exc:
            raise DownloadError(f"Failed to fetch releases.json: {exc}") from exc

        self._releases_cache = data
        versions: List[Dict[str, Any]] = []
        longterm_list: List[str] = []
        stable_ver = ""
        mainline_ver = ""

        # Current kernel.org API: { "latest_stable": {"version": "..."}, "releases": [ {...}, ... ] }
        if isinstance(data.get("releases"), list):
            ls = data.get("latest_stable")
            if isinstance(ls, dict):
                stable_ver = str(ls.get("version") or "")
            for item in data["releases"]:
                if not isinstance(item, dict):
                    continue
                moniker = str(item.get("moniker") or "")
                ver = str(item.get("version") or "")
                if not ver:
                    continue
                if moniker in ("linux-next", "snapshot"):
                    continue
                src = item.get("source")
                source_url = src if isinstance(src, str) and src.startswith("https://") else ""
                if not source_url:
                    source_url = cdn_source_url(ver, self.cdn_url)
                rel = ""
                rel_data = item.get("released")
                if isinstance(rel_data, dict):
                    rel = str(rel_data.get("isodate") or "")
                kind = moniker if moniker in ("stable", "mainline", "longterm") else "other"
                if moniker == "longterm":
                    longterm_list.append(ver)
                if moniker == "mainline":
                    mainline_ver = ver
                is_rc = "rc" in ver.lower()
                if is_rc and not include_rc:
                    continue
                versions.append(
                    {
                        "version": ver,
                        "type": kind,
                        "moniker": moniker,
                        "released": rel,
                        "source_url": source_url,
                        "pgp_url": (source_url + ".sign") if source_url.endswith(".xz") else "",
                        "sha256_url": self._sha256sums_url(ver),
                    }
                )
        else:
            # Legacy shape: stable / mainline / longterm objects
            stable_ver = self._pick_version(data.get("stable")) or stable_ver
            mainline_ver = self._pick_version(data.get("mainline")) or mainline_ver
            lt = data.get("longterm") or data.get("longterm_versions")
            if isinstance(lt, list):
                for item in lt:
                    longterm_list.append(self._pick_version(item))
            elif isinstance(lt, dict):
                longterm_list.append(self._pick_version(lt))

            def add_entry(ver: str, kind: str, moniker: str, released: str, source_url: str) -> None:
                if not ver:
                    return
                is_rc = "rc" in ver.lower()
                if is_rc and not include_rc:
                    return
                versions.append(
                    {
                        "version": ver,
                        "type": kind,
                        "moniker": moniker,
                        "released": released,
                        "source_url": source_url,
                        "pgp_url": source_url + ".sign" if source_url.endswith(".xz") else "",
                        "sha256_url": self._sha256sums_url(ver),
                    }
                )

            if stable_ver:
                url = self._source_url_from_release(data.get("stable"), stable_ver)
                add_entry(
                    stable_ver,
                    "stable",
                    "stable",
                    self._released_date(data.get("stable")),
                    url,
                )
            if mainline_ver:
                url = self._source_url_from_release(data.get("mainline"), mainline_ver)
                add_entry(
                    mainline_ver,
                    "mainline",
                    "mainline",
                    self._released_date(data.get("mainline")),
                    url,
                )
            for lv in longterm_list:
                if lv:
                    add_entry(lv, "longterm", "longterm", "", cdn_source_url(lv, self.cdn_url))

        if not stable_ver and data.get("latest_stable"):
            stable_ver = self._pick_version(data.get("latest_stable"))

        # Deduplicate by version, keep order
        seen = set()
        uniq: List[Dict[str, Any]] = []
        for v in versions:
            ver = v["version"]
            if ver in seen:
                continue
            seen.add(ver)
            uniq.append(v)

        return {
            "stable": stable_ver or "",
            "mainline": mainline_ver or "",
            "longterm": [x for x in longterm_list if x],
            "versions": uniq,
        }

    def _pick_version(self, node: Any) -> str:
        if isinstance(node, dict):
            return str(node.get("version") or "")
        if isinstance(node, str):
            return node
        return ""

    def _released_date(self, node: Any) -> str:
        if not isinstance(node, dict):
            return ""
        rel = node.get("released")
        if isinstance(rel, dict):
            return str(rel.get("isodate") or "")
        return ""

    def _source_url_from_release(self, node: Any, version: str) -> str:
        if isinstance(node, dict):
            src = node.get("source")
            if isinstance(src, str) and src.startswith("http"):
                return src
        return cdn_source_url(version, self.cdn_url)

    def _sha256sums_url(self, version: str) -> str:
        branch = kernel_major_branch(version)
        return f"{self.cdn_url.rstrip('/')}/{branch}/sha256sums.asc"

    def get_latest_stable(self) -> str:
        data = self._releases_cache or self.fetch_kernel_versions()
        return str(data.get("stable") or "")

    def get_latest_mainline(self) -> str:
        data = self._releases_cache or self.fetch_kernel_versions()
        return str(data.get("mainline") or "")

    def get_longterm_versions(self) -> List[str]:
        data = self._releases_cache or self.fetch_kernel_versions()
        return list(data.get("longterm") or [])

    @staticmethod
    def expected_source_directory(parent: Path, version: str) -> Path:
        """Top-level directory name after extracting linux-{version}.* (matches kernel tarballs)."""
        return (parent / f"linux-{version}").resolve()

    @staticmethod
    def is_kernel_source_tree(path: Path) -> bool:
        return path.is_dir() and (path / "Makefile").is_file()

    def download_kernel_source(
        self,
        version: str,
        target_dir: Optional[str] = None,
        verify_signature: bool = True,
        reuse_existing: bool = True,
    ) -> Tuple[str, str]:
        """
        Download (if needed) and extract kernel source.

        Returns:
            (path_to_source_tree, status) where status is ``reuse_tree``, ``reuse_tarball``,
            or ``fresh`` (downloaded new tarball).
        """
        _ = verify_signature  # optional gpg not implemented; checksum always used if available
        meta = self.fetch_kernel_versions()
        url = ""
        for v in meta.get("versions", []):
            if v.get("version") == version:
                url = v.get("source_url") or ""
                break
        if not url:
            url = cdn_source_url(version, self.cdn_url)

        parent = Path(target_dir) if target_dir else project_root() / "data" / "builds"
        parent.mkdir(parents=True, exist_ok=True)
        fname = Path(urlparse(url).path).name
        if not fname or fname.endswith("/"):
            fname = f"linux-{version}.tar.xz"
        tarball = parent / fname
        extract_dir = self.expected_source_directory(parent, version)

        if reuse_existing and self.is_kernel_source_tree(extract_dir):
            self._download_progress = 100.0
            return str(extract_dir), "reuse_tree"

        expected_hash = self._fetch_sha256_for_tarball(version, tarball.name)
        need_download = True
        if reuse_existing and tarball.is_file() and tarball.stat().st_size > 0:
            if expected_hash:
                if self.verify_checksum(str(tarball), expected_hash):
                    need_download = False
                else:
                    tarball.unlink(missing_ok=True)
            else:
                need_download = False

        if need_download:
            self._download_file(url, tarball)
            expected_hash = self._fetch_sha256_for_tarball(version, tarball.name)
            if expected_hash and not self.verify_checksum(str(tarball), expected_hash):
                raise VerificationError("SHA256 checksum mismatch for kernel tarball")
            status = "fresh"
        else:
            if expected_hash and not self.verify_checksum(str(tarball), expected_hash):
                raise VerificationError("SHA256 checksum mismatch for cached kernel tarball")
            status = "reuse_tarball"

        extract_root = parent
        if self.is_kernel_source_tree(extract_dir):
            return str(extract_dir), status
        extracted = self.extract_tarball(str(tarball), str(extract_root))
        return extracted, status

    def _fetch_sha256_for_tarball(self, version: str, filename: str) -> Optional[str]:
        url = self._sha256sums_url(version)
        try:
            r = self.session.get(url, timeout=120)
            r.raise_for_status()
        except requests.RequestException:
            return None
        for line in r.text.splitlines():
            parts = line.split()
            if len(parts) >= 2 and filename in parts[-1]:
                return parts[0].lower()
            if len(parts) >= 2 and parts[1].endswith(filename):
                return parts[0].lower()
        return None

    def _download_file(self, url: str, dest: Path) -> None:
        if not url.startswith("https://"):
            from utils.exceptions import SecurityError

            raise SecurityError("Only HTTPS downloads are allowed")
        self._download_progress = 0.0
        try:
            with self.session.get(url, stream=True, timeout=600) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                dest.parent.mkdir(parents=True, exist_ok=True)
                # Use Rich progress bar when a TTY is available
                use_progress = sys.stderr.isatty() and total > 0
                if use_progress:
                    from utils.ui import progress_download
                    progress = progress_download()
                    task = progress.add_task(f"Downloading {dest.name}", total=total)
                    progress.start()
                try:
                    with open(dest, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=1024 * 256):
                            if not chunk:
                                continue
                            f.write(chunk)
                            done += len(chunk)
                            if total:
                                self._download_progress = min(100.0, (done / total) * 100.0)
                            if use_progress:
                                progress.update(task, advance=len(chunk))  # type: ignore[possibly-undefined]
                finally:
                    if use_progress:
                        progress.stop()  # type: ignore[possibly-undefined]
        except requests.RequestException as exc:
            raise DownloadError(f"Download failed: {exc}") from exc
        self._download_progress = 100.0

    def verify_checksum(self, filepath: str, expected_hash: str) -> bool:
        h = hashlib.sha256()
        p = Path(filepath)
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest().lower() == expected_hash.strip().lower()

    def extract_tarball(self, tarball_path: str, target_dir: str) -> str:
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        tpath = Path(tarball_path)
        if tpath.suffix == ".gz" or tpath.name.endswith(".tar.gz"):
            mode = "r:gz"
        else:
            mode = "r:xz"
        with tarfile.open(tarball_path, mode) as tf:  # type: ignore[arg-type]
            names = tf.getnames()
            if not names:
                raise DownloadError("Empty tarball")
            # Validate all member paths before extraction (path traversal check)
            for name in names:
                safe_extract_path(target, name)
            root_dir = names[0].split("/")[0]
            # Use 'data' filter for security (Python 3.12+); fall back gracefully
            try:
                tf.extractall(path=target, filter="data")
            except TypeError:
                # Python < 3.12 does not support the filter parameter
                tf.extractall(path=target)
        out = target / root_dir
        if not out.is_dir():
            raise DownloadError("Unexpected tarball layout")
        return str(out.resolve())

    def get_download_progress(self) -> float:
        return float(self._download_progress)
