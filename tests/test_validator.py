"""Tests for utils.validator."""

import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from utils.exceptions import SecurityError
from utils.validator import (
    canonical_kernel_release,
    check_file_safety,
    path_is_within,
    safe_extract_tarball,
    validate_backup_id,
    validate_boot_backup_filename,
    validate_build_id,
    validate_kernel_release,
    validate_kernel_version,
    validate_localversion,
)


class TestValidator(unittest.TestCase):
    def test_localversion_is_empty_by_default_or_safe_suffix(self) -> None:
        self.assertTrue(validate_localversion(""))
        self.assertTrue(validate_localversion("-custom.1"))
        self.assertFalse(validate_localversion("getkernel"))
        self.assertFalse(validate_localversion("-bad/path"))

    def test_kernel_release_rejects_paths(self) -> None:
        self.assertTrue(validate_kernel_release("6.12.8-custom"))
        self.assertFalse(validate_kernel_release("../../6.12.8"))

    def test_canonical_kernel_release_adds_kbuild_sublevel(self) -> None:
        self.assertEqual(canonical_kernel_release("6.12.8"), "6.12.8")
        self.assertEqual(canonical_kernel_release("6.13"), "6.13.0")
        self.assertEqual(canonical_kernel_release("6.13-rc1"), "6.13.0-rc1")
        self.assertEqual(
            canonical_kernel_release("6.13-rc1", "-custom"),
            "6.13.0-rc1-custom",
        )

    def test_validate_kernel_version(self) -> None:
        self.assertTrue(validate_kernel_version("6.12.8"))
        self.assertTrue(validate_kernel_version("6.13-rc1"))
        self.assertTrue(validate_kernel_version("6.13-beta1"))
        self.assertFalse(validate_kernel_version("../6.12.8"))
        self.assertFalse(validate_kernel_version(""))

    def test_validate_backup_and_build_ids(self) -> None:
        self.assertTrue(validate_backup_id("backup-20260707-155257"))
        self.assertFalse(validate_backup_id("../etc"))
        self.assertTrue(validate_build_id("a1b2c3d4e5f6"))
        self.assertFalse(validate_build_id("../../outside"))

    def test_validate_boot_backup_filename(self) -> None:
        self.assertTrue(validate_boot_backup_filename("vmlinuz-6.12.8-getkernel"))
        self.assertFalse(validate_boot_backup_filename("../etc/passwd"))
        self.assertFalse(validate_boot_backup_filename("evil.sh"))

    def test_path_is_within(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            child = root / "nested"
            child.mkdir()
            self.assertTrue(path_is_within(child, root))
            self.assertFalse(path_is_within(Path("/tmp"), root))

    def test_check_file_safety(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            deb = Path(td) / "linux-image-test.deb"
            deb.write_bytes(b"data")
            ok, _ = check_file_safety(deb)
            self.assertTrue(ok)
            txt = Path(td) / "not-a-deb.txt"
            txt.write_text("x", encoding="utf-8")
            ok2, msg = check_file_safety(txt)
            self.assertFalse(ok2)
            self.assertIn("deb", msg)

    def test_safe_extract_tarball_rejects_symlink(self) -> None:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo(name="link")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tf.addfile(info)
        data = buf.getvalue()
        with tempfile.TemporaryDirectory() as td:
            target = Path(td)
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tf:
                with self.assertRaises(SecurityError):
                    safe_extract_tarball(tf, target)

    def test_safe_extract_tarball_preserves_mode_and_safe_relative_symlink(self) -> None:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            data = b"#!/bin/sh\n"
            script = tarfile.TarInfo(name="linux-test/script.sh")
            script.size = len(data)
            script.mode = 0o755
            tf.addfile(script, io.BytesIO(data))
            link = tarfile.TarInfo(name="linux-test/script-link")
            link.type = tarfile.SYMTYPE
            link.linkname = "script.sh"
            tf.addfile(link)

        with tempfile.TemporaryDirectory() as td:
            target = Path(td)
            with tarfile.open(fileobj=io.BytesIO(buf.getvalue()), mode="r:") as tf:
                safe_extract_tarball(tf, target)
            extracted = target / "linux-test" / "script.sh"
            self.assertEqual(extracted.stat().st_mode & 0o777, 0o755)
            self.assertEqual((target / "linux-test" / "script-link").read_bytes(), data)


if __name__ == "__main__":
    unittest.main()
