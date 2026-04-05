"""Reuse of existing kernel tree / tarball."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.kernel_fetcher import KernelFetcher


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


if __name__ == "__main__":
    unittest.main()
