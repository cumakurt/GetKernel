"""Tests for package_builder stored package detection."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_legacy_fallback_does_not_reuse_custom_suffix_for_empty_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            latest = Path(td) / "latest"
            latest.mkdir()
            (latest / "linux-image-6.1.0-getkernel_1_amd64.deb").write_bytes(b"old")
            self.assertIsNone(find_matching_stored_packages(Path(td), "6.1.0", ""))

    def test_legacy_fallback_uses_canonical_rc_release(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            latest = Path(td) / "latest"
            latest.mkdir()
            image = latest / "linux-image-6.13.0-rc1_1_amd64.deb"
            image.write_bytes(b"old")
            self.assertEqual(
                find_matching_stored_packages(Path(td), "6.13-rc1", ""),
                [image],
            )

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

    def test_build_info_rejects_package_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            latest = root / "latest"
            latest.mkdir()
            outside = root / "outside.deb"
            outside.write_bytes(b"deb")
            (latest / BUILD_INFO_FILENAME).write_text(
                json.dumps(
                    {
                        "requested_version": "6.1.0",
                        "localversion": "",
                        "deb_names": ["../outside.deb"],
                    }
                ),
                encoding="utf-8",
            )
            self.assertIsNone(find_matching_stored_packages(root, "6.1.0", ""))

    def test_move_packages_replaces_latest_as_one_build_set(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source"
            source.mkdir()
            output = root / "out"
            latest = output / "latest"
            latest.mkdir(parents=True)
            stale = latest / "linux-image-6.0.0_1_amd64.deb"
            stale.write_bytes(b"stale")
            package = root / "linux-image-6.1.0_1_amd64.deb"
            package.write_bytes(b"new")

            pb = PackageBuilder(str(source), output_dir=str(output))
            moved = pb.move_packages(
                [package],
                requested_version="6.1.0",
                localversion="",
                kernel_release="6.1.0",
            )

            self.assertFalse(stale.exists())
            self.assertEqual([p.name for p in moved], [package.name])
            metadata = json.loads((latest / BUILD_INFO_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(metadata["kernel_release"], "6.1.0")

    def test_move_packages_keeps_previous_latest_when_copy_fails(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source"
            source.mkdir()
            output = root / "out"
            latest = output / "latest"
            latest.mkdir(parents=True)
            previous = latest / "linux-image-6.0.0_1_amd64.deb"
            previous.write_bytes(b"previous")
            package = root / "linux-image-6.1.0_1_amd64.deb"
            package.write_bytes(b"new")
            pb = PackageBuilder(str(source), output_dir=str(output))

            with patch("modules.package_builder.shutil.copy2", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    pb.move_packages([package])

            self.assertEqual(previous.read_bytes(), b"previous")
            self.assertFalse(any(output.glob(".latest-stage-*")))

    def test_verify_requires_matching_headers_for_external_modules(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source"
            source.mkdir()
            image = root / "linux-image-6.1.0_1_amd64.deb"
            image.write_bytes(b"deb")
            pb = PackageBuilder(str(source), output_dir=str(root / "out"))
            with patch("modules.package_builder.run_cmd") as command:
                command.return_value.returncode = 0
                with patch.object(
                    pb,
                    "get_package_info",
                    return_value={"package": "linux-image-6.1.0"},
                ):
                    ok, errors = pb.verify_packages(
                        [image],
                        expected_kernel_release="6.1.0",
                        require_headers=True,
                    )
            self.assertFalse(ok)
            self.assertTrue(any("linux-headers" in error for error in errors))

    def test_verify_detects_tampered_depot_package(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source"
            source.mkdir()
            latest = root / "out" / "latest"
            latest.mkdir(parents=True)
            package = latest / "linux-image-6.1.0_1_amd64.deb"
            package.write_bytes(b"tampered")
            (latest / "checksums.sha256").write_text(
                f"{'0' * 64}  {package.name}\n",
                encoding="utf-8",
            )
            pb = PackageBuilder(str(source), output_dir=str(root / "out"))
            with patch("modules.package_builder.run_cmd") as command:
                command.return_value.returncode = 0
                with patch.object(
                    pb,
                    "get_package_info",
                    return_value={"package": "linux-image-6.1.0"},
                ):
                    ok, errors = pb.verify_packages([package])

            self.assertFalse(ok)
            self.assertTrue(any("checksum mismatch" in error for error in errors))

    def test_verify_detects_package_missing_from_depot_set(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source"
            source.mkdir()
            latest = root / "out" / "latest"
            latest.mkdir(parents=True)
            package = latest / "linux-image-6.1.0_1_amd64.deb"
            package.write_bytes(b"image")
            missing_name = "linux-headers-6.1.0_1_amd64.deb"
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            (latest / "checksums.sha256").write_text(
                f"{digest}  {package.name}\n{'0' * 64}  {missing_name}\n",
                encoding="utf-8",
            )
            pb = PackageBuilder(str(source), output_dir=str(root / "out"))
            with patch("modules.package_builder.run_cmd") as command:
                command.return_value.returncode = 0
                with patch.object(
                    pb,
                    "get_package_info",
                    return_value={"package": "linux-image-6.1.0"},
                ):
                    ok, errors = pb.verify_packages([package])

            self.assertFalse(ok)
            self.assertTrue(any("missing depot package" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
