"""Tests for package depot helpers."""

import json
import tempfile
import unittest
from pathlib import Path

from modules.package_builder import BUILD_INFO_FILENAME
from modules.package_depot import (
    archive_latest_to_build_id,
    list_archived_builds,
    list_latest_packages,
    resolve_package_paths,
    write_build_history_entry,
)


class TestPackageDepot(unittest.TestCase):
    def test_list_and_resolve_latest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            latest = root / "latest"
            latest.mkdir(parents=True)
            deb = latest / "linux-image-test_1_amd64.deb"
            deb.write_bytes(b"deb")
            (latest / BUILD_INFO_FILENAME).write_text(
                json.dumps({"requested_version": "6.1.0", "built_at": "2024-01-01"}),
                encoding="utf-8",
            )
            rows = list_latest_packages(root)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["name"], deb.name)
            resolved = resolve_package_paths(root)
            self.assertEqual(len(resolved), 1)

    def test_archive_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            latest = root / "latest"
            latest.mkdir(parents=True)
            deb = latest / "linux-image-test_1_amd64.deb"
            deb.write_bytes(b"deb")
            (latest / BUILD_INFO_FILENAME).write_text("{}", encoding="utf-8")
            archived = archive_latest_to_build_id(root, "abc123")
            self.assertIsNotNone(archived)
            assert archived is not None
            self.assertTrue((archived / deb.name).is_file())
            write_build_history_entry(root, "abc123", {"requested_version": "6.1.0"})
            archives = list_archived_builds(root)
            self.assertEqual(len(archives), 1)
            self.assertEqual(archives[0]["build_id"], "abc123")
            resolved = resolve_package_paths(root, build_id="abc123")
            self.assertEqual(len(resolved), 1)


if __name__ == "__main__":
    unittest.main()
