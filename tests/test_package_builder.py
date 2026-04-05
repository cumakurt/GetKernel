"""Tests for package_builder stored package detection."""

import json
import tempfile
import unittest
from pathlib import Path

from modules.package_builder import (
    BUILD_INFO_FILENAME,
    PackageBuilder,
    find_matching_stored_packages,
)


class TestFindMatchingStored(unittest.TestCase):
    def test_build_info_exact_match(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            latest = root / "latest"
            latest.mkdir(parents=True)
            (latest / "linux-image-6.1.0-test_6.1.0-1_amd64.deb").write_bytes(b"x" * 100)
            data = {
                "requested_version": "6.1.0",
                "localversion": "-test",
                "deb_names": ["linux-image-6.1.0-test_6.1.0-1_amd64.deb"],
            }
            (latest / BUILD_INFO_FILENAME).write_text(
                json.dumps(data), encoding="utf-8"
            )
            found = find_matching_stored_packages(root, "6.1.0", "-test")
            self.assertIsNotNone(found)
            self.assertEqual(len(found), 1)

    def test_build_info_version_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            latest = root / "latest"
            latest.mkdir(parents=True)
            (latest / BUILD_INFO_FILENAME).write_text(
                json.dumps(
                    {
                        "requested_version": "6.1.0",
                        "localversion": "-test",
                        "deb_names": [],
                    }
                ),
                encoding="utf-8",
            )
            found = find_matching_stored_packages(root, "6.2.0", "-test")
            self.assertIsNone(found)

    def test_cleanup_dry_run_does_not_delete(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake_tree = root / "linux-6.1"
            fake_tree.mkdir()
            junk = fake_tree / "foo.o"
            junk.write_bytes(b"x")
            pb = PackageBuilder(str(fake_tree), output_dir=str(root / "out"))
            n = pb.cleanup_build_artifacts(keep_packages=True, dry_run=True)
            self.assertGreaterEqual(n, 1)
            self.assertTrue(junk.is_file(), "dry-run must not unlink files")


if __name__ == "__main__":
    unittest.main()
