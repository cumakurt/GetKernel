"""Fetch kernel versions and sources from kernel.org."""

from __future__ import annotations

import hashlib
import json
import sys
import tarfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

import requests

from utils.constants import (
    APP_VERSION,
    CDN_MIRRORS,
    DEVELOPER_GITHUB_REPO_URL,
    KERNEL_ORG_RELEASES_JSON,
)
from utils.exceptions import DownloadError, VerificationError
from utils.helpers import cdn_source_url, kernel_major_branch, project_root, run_cmd
from utils.validator import safe_extract_path, safe_extract_tarball


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
        self.verify_checksum_enabled = True
        self.verify_signature_enabled = False
        self.include_beta = True
        self.include_rc = True

    @classmethod
    def from_config(
        cls,
        cache_dir: Optional[str],
        kernel_cfg: Mapping[str, Any],
    ) -> "KernelFetcher":
        fetcher = cls(cache_dir)
        fetcher.verify_checksum_enabled = bool(kernel_cfg.get("verify_checksum", True))
        fetcher.verify_signature_enabled = bool(kernel_cfg.get("verify_signature", False))
        fetcher.include_beta = bool(kernel_cfg.get("include_beta", True))
        fetcher.include_rc = bool(kernel_cfg.get("include_rc", True))
        return fetcher

    def fetch_kernel_versions(
        self,
        include_beta: Optional[bool] = None,
        include_rc: Optional[bool] = None,
    ) -> Dict[str, Any]:
        ib = self.include_beta if include_beta is None else include_beta
        ir = self.include_rc if include_rc is None else include_rc
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
                if is_rc and not ir:
                    continue
                is_beta = "beta" in ver.lower()
                if is_beta and not ib:
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
                if is_rc and not ir:
                    return
                is_beta = "beta" in ver.lower()
                if is_beta and not ib:
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
            (path_to_source_tree, status) where status is ``reuse_tree``,
            ``reuse_tarball``, ``resume`` (continued partial download), or ``fresh``.
        """
        do_verify_sig = verify_signature and self.verify_signature_enabled
        meta = self.fetch_kernel_versions()
        url = ""
        pgp_url = ""
        for v in meta.get("versions", []):
            if v.get("version") == version:
                url = v.get("source_url") or ""
                pgp_url = v.get("pgp_url") or ""
                break
        if not url:
            url = cdn_source_url(version, self.cdn_url)
        if not pgp_url and url.endswith(".xz"):
            pgp_url = url + ".sign"

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
        remote_size = self._head_content_length(url)
        resume_from = 0
        need_download = True
        status = "fresh"

        if reuse_existing and tarball.is_file():
            tarball_state = self._classify_tarball(
                tarball, expected_hash=expected_hash, remote_size=remote_size
            )
            if tarball_state == "complete":
                need_download = False
                status = "reuse_tarball"
            elif tarball_state == "partial":
                resume_from = tarball.stat().st_size
                status = "resume"
            else:
                tarball.unlink(missing_ok=True)
                need_download = True
                status = "fresh"

        if need_download:
            self._download_from_mirrors(version, url, tarball, start_byte=resume_from)
            expected_hash = self._fetch_sha256_for_tarball(version, tarball.name)
            self._assert_tarball_integrity(tarball, expected_hash)
            if do_verify_sig and pgp_url:
                self._verify_gpg_signature(tarball, pgp_url)
            if status != "resume":
                status = "fresh"
        else:
            self._assert_tarball_integrity(tarball, expected_hash)
            if do_verify_sig and pgp_url:
                self._verify_gpg_signature(tarball, pgp_url)

        extract_root = parent
        if self.is_kernel_source_tree(extract_dir):
            return str(extract_dir), status
        extracted = self.extract_tarball(str(tarball), str(extract_root))
        return extracted, status

    def _classify_tarball(
        self,
        tarball: Path,
        *,
        expected_hash: Optional[str],
        remote_size: Optional[int],
    ) -> str:
        """
        Classify an on-disk tarball as complete, partial, or corrupt.

        ``complete`` — checksum OK, or size matches remote when no hash is published.
        ``partial`` — smaller than remote (or unreadable archive) and may be resumed.
        ``corrupt`` — wrong checksum with full size, empty, or unusable; delete and retry.
        """
        if not tarball.is_file() or tarball.stat().st_size <= 0:
            return "corrupt"

        size = tarball.stat().st_size
        if expected_hash and self.verify_checksum_enabled:
            if self.verify_checksum(str(tarball), expected_hash):
                return "complete"
        elif expected_hash is None or not self.verify_checksum_enabled:
            if remote_size and size >= remote_size and self._tarball_is_valid_archive(tarball):
                return "complete"

        if expected_hash and self.verify_checksum_enabled:
            if remote_size and size < remote_size:
                return "partial"
            return "corrupt"

        if remote_size:
            if size < remote_size:
                return "partial"
            if size >= remote_size and self._tarball_is_valid_archive(tarball):
                return "complete"
            return "corrupt"

        if self._tarball_is_valid_archive(tarball):
            return "complete"
        return "corrupt"

    @staticmethod
    def _tarball_is_valid_archive(path: Path) -> bool:
        """Lightweight integrity probe when no SHA256 is available."""
        try:
            if path.name.endswith(".tar.gz") or path.suffix == ".gz":
                mode = "r:gz"
            else:
                mode = "r:xz"
            with tarfile.open(path, mode) as tf:  # type: ignore[arg-type]
                if not tf.getnames():
                    return False
            return True
        except (tarfile.TarError, OSError):
            return False

    def _head_content_length(self, url: str) -> Optional[int]:
        try:
            resp = self.session.head(url, timeout=60, allow_redirects=True)
            resp.raise_for_status()
            raw = resp.headers.get("Content-Length")
            if raw and str(raw).isdigit():
                return int(raw)
        except requests.RequestException:
            return None
        return None

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

    def _assert_tarball_integrity(
        self,
        tarball: Path,
        expected_hash: Optional[str],
    ) -> None:
        if not self.verify_checksum_enabled or not expected_hash:
            return
        if not self.verify_checksum(str(tarball), expected_hash):
            raise VerificationError("SHA256 checksum mismatch for kernel tarball")

    def _mirror_urls(self, version: str, primary_url: str) -> List[str]:
        urls: List[str] = []
        for mirror in CDN_MIRRORS:
            candidate = cdn_source_url(version, mirror)
            if candidate not in urls:
                urls.append(candidate)
        if primary_url and primary_url not in urls:
            urls.insert(0, primary_url)
        return urls

    def _download_from_mirrors(
        self,
        version: str,
        primary_url: str,
        dest: Path,
        *,
        start_byte: int = 0,
    ) -> None:
        errors: List[str] = []
        for url in self._mirror_urls(version, primary_url):
            try:
                self._download_file(url, dest, start_byte=start_byte)
                return
            except DownloadError as exc:
                errors.append(f"{url}: {exc}")
                if dest.is_file() and start_byte == 0:
                    dest.unlink(missing_ok=True)
        raise DownloadError(
            "Download failed on all CDN mirrors:\n" + "\n".join(errors)
        )

    def _verify_gpg_signature(self, tarball: Path, sign_url: str) -> None:
        sign_path = tarball.with_name(tarball.name + ".sign")
        try:
            resp = self.session.get(sign_url, timeout=120)
            resp.raise_for_status()
            sign_path.write_bytes(resp.content)
        except requests.RequestException as exc:
            raise VerificationError(f"Failed to download signature: {exc}") from exc
        cp = run_cmd(["gpg", "--verify", str(sign_path), str(tarball)])
        if cp.returncode != 0:
            detail = (cp.stderr or cp.stdout or "").strip()
            raise VerificationError(f"GPG verification failed: {detail}")

    def _download_file(self, url: str, dest: Path, start_byte: int = 0) -> None:
        if not url.startswith("https://"):
            from utils.exceptions import SecurityError

            raise SecurityError("Only HTTPS downloads are allowed")

        headers: Dict[str, str] = {}
        mode = "wb"
        done = 0
        if start_byte > 0:
            headers["Range"] = f"bytes={start_byte}-"
            mode = "ab"
            done = start_byte

        self._download_progress = (done / max(done, 1)) * 100.0 if done else 0.0
        try:
            with self.session.get(
                url, stream=True, headers=headers, timeout=600
            ) as resp:
                if start_byte > 0:
                    if resp.status_code == 416:
                        raise DownloadError(
                            "Resume failed: server rejected the byte range "
                            f"(local size {start_byte} bytes)."
                        )
                    if resp.status_code == 200:
                        mode = "wb"
                        done = 0
                        start_byte = 0
                    elif resp.status_code != 206:
                        resp.raise_for_status()
                else:
                    resp.raise_for_status()

                chunk_total = int(resp.headers.get("Content-Length") or 0)
                if start_byte > 0 and resp.status_code == 206:
                    total = start_byte + chunk_total
                else:
                    total = chunk_total

                dest.parent.mkdir(parents=True, exist_ok=True)
                use_progress = sys.stderr.isatty() and total > 0
                if use_progress:
                    from utils.ui import progress_download

                    progress = progress_download()
                    label = (
                        f"Resuming {dest.name}"
                        if start_byte > 0
                        else f"Downloading {dest.name}"
                    )
                    task = progress.add_task(label, total=total, completed=done)
                    progress.start()
                try:
                    with open(dest, mode) as f:
                        for chunk in resp.iter_content(chunk_size=1024 * 256):
                            if not chunk:
                                continue
                            f.write(chunk)
                            done += len(chunk)
                            if total:
                                self._download_progress = min(
                                    100.0, (done / total) * 100.0
                                )
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
            try:
                tf.extractall(path=target, filter="data")
            except TypeError:
                safe_extract_tarball(tf, target)
        out = target / root_dir
        if not out.is_dir():
            raise DownloadError("Unexpected tarball layout")
        return str(out.resolve())

    def get_download_progress(self) -> float:
        return float(self._download_progress)
