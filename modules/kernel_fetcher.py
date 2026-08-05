"""Fetch kernel versions and sources from kernel.org."""

from __future__ import annotations

import gzip
import hashlib
import json
import lzma
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
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
from utils.helpers import cdn_source_url, kernel_major_branch, project_root
from utils.validator import safe_extract_tarball


class KernelFetcher:
    """Download metadata and kernel tarballs from kernel.org."""

    RELEASE_CACHE_TTL_SEC = 15 * 60
    RELEASE_CACHE_MAX_BYTES = 5 * 1024 * 1024

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
        data = self._read_release_cache(max_age=self.RELEASE_CACHE_TTL_SEC)
        if data is None:
            try:
                r = self.session.get(self.releases_api, timeout=60)
                r.raise_for_status()
                parsed = r.json()
                if not self._is_release_payload(parsed):
                    raise ValueError("response does not contain kernel release data")
                data = parsed
                self._write_release_cache(data)
            except (requests.RequestException, json.JSONDecodeError, ValueError) as exc:
                data = self._read_release_cache(max_age=None)
                if data is None:
                    raise DownloadError(f"Failed to fetch releases.json: {exc}") from exc

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
                        "pgp_url": self._signature_url(
                            source_url,
                            str(item.get("pgp") or ""),
                        ),
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
                        "pgp_url": self._signature_url(source_url),
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

        normalized = {
            "stable": stable_ver or "",
            "mainline": mainline_ver or "",
            "longterm": [x for x in longterm_list if x],
            "versions": uniq,
        }
        # Helpers consume the normalized public shape, not kernel.org's raw
        # schema (which uses latest_stable instead of stable).
        self._releases_cache = normalized
        return normalized

    @property
    def _release_cache_path(self) -> Path:
        return self.cache_dir / "releases.json"

    @staticmethod
    def _is_release_payload(data: Any) -> bool:
        if not isinstance(data, dict):
            return False
        if isinstance(data.get("releases"), list):
            return True
        return any(key in data for key in ("stable", "mainline", "longterm"))

    def _read_release_cache(self, *, max_age: Optional[float]) -> Optional[Dict[str, Any]]:
        path = self._release_cache_path
        try:
            stat = path.stat()
            if stat.st_size > self.RELEASE_CACHE_MAX_BYTES:
                return None
            if max_age is not None and time.time() - stat.st_mtime > max_age:
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return None
        return data if self._is_release_payload(data) else None

    def _write_release_cache(self, data: Dict[str, Any]) -> None:
        temp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=self.cache_dir,
                prefix=".releases-",
                encoding="utf-8",
                delete=False,
            ) as temp:
                json.dump(data, temp)
                temp.flush()
                os.fsync(temp.fileno())
                temp_path = Path(temp.name)
            temp_path.replace(self._release_cache_path)
        except OSError:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

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
            if isinstance(src, str) and src.startswith("https://"):
                return src
        return cdn_source_url(version, self.cdn_url)

    @staticmethod
    def _signature_url(source_url: str, explicit_url: str = "") -> str:
        if explicit_url.startswith("https://"):
            return explicit_url
        parsed = urlparse(source_url)
        if (
            parsed.hostname in {"cdn.kernel.org", "mirrors.edge.kernel.org", "www.kernel.org"}
            and source_url.endswith((".tar.xz", ".tar.gz"))
        ):
            return source_url.rsplit(".", 1)[0] + ".sign"
        return ""

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
        return all(
            (
                path.is_dir(),
                (path / "Makefile").is_file(),
                (path / "Kconfig").is_file(),
                (path / "scripts" / "kconfig").is_dir(),
            )
        )

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
        parent = Path(target_dir) if target_dir else project_root() / "data" / "builds"
        parent.mkdir(parents=True, exist_ok=True)
        extract_dir = self.expected_source_directory(parent, version)

        # A complete local tree needs no metadata lookup. Repeat builds are
        # therefore instant and remain usable while offline.
        if reuse_existing and self.is_kernel_source_tree(extract_dir):
            self._download_progress = 100.0
            return str(extract_dir), "reuse_tree"

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
        if not pgp_url:
            pgp_url = self._signature_url(url)
        if do_verify_sig and not pgp_url:
            raise VerificationError(
                f"No detached PGP signature is published for linux-{version}. "
                "Choose a signed stable/LTS release or disable signature verification."
            )

        fname = Path(urlparse(url).path).name
        if not fname or fname.endswith("/"):
            fname = f"linux-{version}.tar.xz"
        tarball = parent / fname
        expected_hash = self._fetch_sha256_for_tarball(version, tarball.name)
        self._assert_checksum_available(expected_hash, tarball.name)
        remote_size = None
        if reuse_existing and tarball.is_file():
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
            used_offset = self._download_from_mirrors(
                version, url, tarball, start_byte=resume_from
            )
            if used_offset == 0 and resume_from > 0:
                status = "fresh"
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
        if not self.is_kernel_source_tree(Path(extracted)):
            raise DownloadError(
                "Extracted archive is not a complete kernel source tree "
                "(missing Makefile, Kconfig, or scripts/kconfig)."
            )
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
                found = False
                for _member in tf:
                    found = True
                if not found:
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
            if len(parts) < 2:
                continue
            digest = parts[0].lower()
            listed_name = parts[-1].lstrip("*")
            if (
                Path(listed_name).name == filename
                and len(digest) == 64
                and all(c in "0123456789abcdef" for c in digest)
            ):
                return digest
        return None

    def _assert_tarball_integrity(
        self,
        tarball: Path,
        expected_hash: Optional[str],
    ) -> None:
        if not self.verify_checksum_enabled:
            return
        self._assert_checksum_available(expected_hash, tarball.name)
        if not self.verify_checksum(str(tarball), expected_hash):
            raise VerificationError("SHA256 checksum mismatch for kernel tarball")

    def _assert_checksum_available(
        self,
        expected_hash: Optional[str],
        filename: str,
    ) -> None:
        if self.verify_checksum_enabled and not expected_hash:
            raise VerificationError(
                f"Checksum verification is enabled, but kernel.org did not provide "
                f"a SHA256 entry for {filename}. This is common for generated "
                "mainline/RC snapshots. Set kernel.verify_checksum: false only "
                "if HTTPS transport plus archive validation is acceptable."
            )

    def _mirror_urls(self, version: str, primary_url: str) -> List[str]:
        canonical_name = Path(urlparse(cdn_source_url(version, self.cdn_url)).path).name
        primary_name = Path(urlparse(primary_url).path).name if primary_url else ""
        if primary_url and primary_name != canonical_name:
            # kernel.org mainline metadata can point to a generated .tar.gz
            # git snapshot. A CDN .tar.xz is not a byte-identical mirror and
            # must never be written under the snapshot filename.
            return [primary_url]
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
    ) -> int:
        """Try mirrors safely, then retry a failed resume as a fresh download."""
        errors: List[str] = []
        offsets = [start_byte]
        if start_byte > 0:
            offsets.append(0)
        for offset in offsets:
            for url in self._mirror_urls(version, primary_url):
                try:
                    # A failed mirror may have written bytes. Restore the exact
                    # starting state before trying another one; otherwise the
                    # next response would be appended after a corrupt tail.
                    if dest.is_file():
                        if offset == 0:
                            dest.unlink(missing_ok=True)
                        else:
                            with open(dest, "r+b") as partial:
                                partial.truncate(offset)
                    return self._download_file(url, dest, start_byte=offset)
                except (DownloadError, OSError) as exc:
                    mode = "resume" if offset else "fresh"
                    errors.append(f"{url} ({mode}): {exc}")
        raise DownloadError(
            "Download failed on all CDN mirrors:\n" + "\n".join(errors)
        )

    def _verify_gpg_signature(self, tarball: Path, sign_url: str) -> None:
        sign_path = tarball.with_name(tarball.name + ".sign")
        try:
            resp = self.session.get(sign_url, timeout=120)
            resp.raise_for_status()
            sign_path.write_bytes(resp.content)
        except (requests.RequestException, OSError) as exc:
            raise VerificationError(f"Failed to download signature: {exc}") from exc
        # kernel.org signs the uncompressed .tar stream so the same signature
        # works for xz/gz variants. Feed decompressed bytes to gpg without
        # materialising another multi-gigabyte file.
        opener = gzip.open if tarball.name.endswith(".gz") else lzma.open
        try:
            proc = subprocess.Popen(
                ["gpg", "--verify", str(sign_path), "-"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise VerificationError(f"Cannot start gpg: {exc}") from exc
        try:
            assert proc.stdin is not None
            with opener(tarball, "rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    proc.stdin.write(chunk)
            proc.stdin.close()
            proc.stdin = None
            stdout, stderr = proc.communicate(timeout=600)
        except (OSError, EOFError, lzma.LZMAError, subprocess.TimeoutExpired) as exc:
            proc.kill()
            proc.communicate()
            raise VerificationError(f"GPG verification could not complete: {exc}") from exc
        if proc.returncode != 0:
            detail = (stderr or stdout or b"").decode(errors="replace").strip()
            raise VerificationError(f"GPG verification failed: {detail}")

    def _download_file(self, url: str, dest: Path, start_byte: int = 0) -> int:
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

        self._download_progress = 0.0
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
                        content_range = resp.headers.get("Content-Range", "")
                        if not content_range.startswith(f"bytes {start_byte}-"):
                            raise DownloadError(
                                "Resume failed: server returned an unexpected Content-Range."
                            )
                else:
                    resp.raise_for_status()

                raw_length = resp.headers.get("Content-Length") or "0"
                chunk_total = int(raw_length) if str(raw_length).isdigit() else 0
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
                        for chunk in resp.iter_content(chunk_size=1024 * 1024):
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
                if total and done != total:
                    raise DownloadError(
                        f"Incomplete download: received {done} of {total} bytes."
                    )
        except (requests.RequestException, OSError) as exc:
            raise DownloadError(f"Download failed: {exc}") from exc
        self._download_progress = 100.0
        # Report the offset actually used. A server may legally ignore Range
        # and return 200, in which case the file was restarted rather than
        # resumed and the CLI should say so.
        return start_byte

    def verify_checksum(self, filepath: str, expected_hash: str) -> bool:
        h = hashlib.sha256()
        p = Path(filepath)
        try:
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
        except OSError as exc:
            raise VerificationError(f"Cannot read tarball for SHA256 verification: {exc}") from exc
        return h.hexdigest().lower() == expected_hash.strip().lower()

    def extract_tarball(self, tarball_path: str, target_dir: str) -> str:
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        tpath = Path(tarball_path)
        if tpath.suffix == ".gz" or tpath.name.endswith(".tar.gz"):
            mode = "r:gz"
        else:
            mode = "r:xz"
        stage = Path(tempfile.mkdtemp(prefix=".getkernel-extract-", dir=target))
        previous: Optional[Path] = None
        out: Optional[Path] = None
        try:
            with tarfile.open(tarball_path, mode) as tf:  # type: ignore[arg-type]
                try:
                    tf.extractall(path=stage, filter="data")
                except TypeError:
                    safe_extract_tarball(tf, stage)

            # Inspect the materialised staging directory instead of scanning
            # the compressed tar once for layout and then decompressing it a
            # second time for extraction. Kernel archives are large, so this
            # removes an entire decompression pass while retaining the single
            # top-level-directory requirement.
            top_level = list(stage.iterdir())
            if len(top_level) != 1 or not top_level[0].is_dir():
                raise DownloadError(
                    "Unexpected tarball layout (expected one root directory)"
                )
            staged_root = top_level[0]
            root_dir = staged_root.name
            out = target / root_dir
            if out.exists():
                previous = target / f".{root_dir}.previous-{uuid.uuid4().hex}"
                out.replace(previous)
            staged_root.replace(out)
        except (tarfile.TarError, OSError) as exc:
            if previous is not None and out is not None and not out.exists():
                previous.replace(out)
            raise DownloadError(f"Cannot extract kernel tarball: {exc}") from exc
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        if previous is not None:
            shutil.rmtree(previous, ignore_errors=True)
        if out is None:
            raise DownloadError("Unexpected tarball layout")
        if not out.is_dir():
            raise DownloadError("Unexpected tarball layout")
        return str(out.resolve())

    def get_download_progress(self) -> float:
        return float(self._download_progress)
