"""Reuse of existing kernel tree / tarball and resume downloads."""

import hashlib
import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from modules.kernel_fetcher import KernelFetcher


def _make_min_xz_tarball(path: Path, root_name: str = "linux-test") -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        data = b"# dummy\n"
        info = tarfile.TarInfo(name=f"{root_name}/Makefile")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    import lzma

    path.write_bytes(lzma.compress(buf.getvalue()))


class TestKernelReuse(unittest.TestCase):
    def test_reuse_tree_skips_network(self) -> None:
        meta = {"versions": [], "stable": "", "mainline": "", "longterm": []}
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td)
            ver = "9.9.9-test"
            src = parent / f"linux-{ver}"
            src.mkdir()
            (src / "Makefile").write_text("# dummy\n", encoding="utf-8")
            f = KernelFetcher(cache_dir=str(parent / "cache"))
            with patch.object(KernelFetcher, "fetch_kernel_versions", return_value=meta):
                path, status = f.download_kernel_source(
                    ver,
                    target_dir=str(parent),
                    reuse_existing=True,
                )
            self.assertEqual(status, "reuse_tree")
            self.assertEqual(Path(path).resolve(), src.resolve())

    def test_reuse_tarball_when_checksum_matches(self) -> None:
        meta = {
            "versions": [
                {
                    "version": "6.1.0",
                    "source_url": "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.0.tar.xz",
                }
            ],
            "stable": "",
            "mainline": "",
            "longterm": [],
        }
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td)
            tarball = parent / "linux-6.1.0.tar.xz"
            _make_min_xz_tarball(tarball, "linux-6.1.0")
            digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
            f = KernelFetcher(cache_dir=str(parent / "cache"))
            with patch.object(KernelFetcher, "fetch_kernel_versions", return_value=meta):
                with patch.object(
                    f, "_fetch_sha256_for_tarball", return_value=digest
                ):
                    with patch.object(f, "_head_content_length", return_value=tarball.stat().st_size):
                        with patch.object(f, "_download_file") as mock_dl:
                            path, status = f.download_kernel_source(
                                "6.1.0",
                                target_dir=str(parent),
                                reuse_existing=True,
                            )
            mock_dl.assert_not_called()
            self.assertEqual(status, "reuse_tarball")
            self.assertTrue((Path(path) / "Makefile").is_file())

    def test_resume_partial_tarball(self) -> None:
        meta = {
            "versions": [
                {
                    "version": "6.2.0",
                    "source_url": "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.2.0.tar.xz",
                }
            ],
            "stable": "",
            "mainline": "",
            "longterm": [],
        }
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td)
            tarball = parent / "linux-6.2.0.tar.xz"
            tarball.write_bytes(b"partial-data")
            remote_size = 4096
            f = KernelFetcher(cache_dir=str(parent / "cache"))
            with patch.object(KernelFetcher, "fetch_kernel_versions", return_value=meta):
                with patch.object(f, "_fetch_sha256_for_tarball", return_value="abc123"):
                    with patch.object(f, "_head_content_length", return_value=remote_size):
                        with patch.object(f, "_download_file") as mock_dl:
                            with patch.object(
                                f, "verify_checksum", side_effect=[False, True]
                            ):
                                with patch.object(
                                    f,
                                    "extract_tarball",
                                    return_value=str(parent / "linux-6.2.0"),
                                ):
                                    _, status = f.download_kernel_source(
                                        "6.2.0",
                                        target_dir=str(parent),
                                        reuse_existing=True,
                                    )
            mock_dl.assert_called_once()
            self.assertEqual(mock_dl.call_args.kwargs.get("start_byte"), len(b"partial-data"))
            self.assertEqual(status, "resume")

    def test_classify_tarball_partial_without_hash(self) -> None:
        f = KernelFetcher(cache_dir="/tmp/getkernel-test-cache")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "linux-6.3.0.tar.xz"
            path.write_bytes(b"partial")
            state = f._classify_tarball(path, expected_hash=None, remote_size=1000)
            self.assertEqual(state, "partial")


if __name__ == "__main__":
    unittest.main()
