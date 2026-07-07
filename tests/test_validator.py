"""Tests for utils.validator."""

import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from utils.exceptions import SecurityError
from utils.validator import (
    check_file_safety,
    path_is_within,
    safe_extract_tarball,
    validate_backup_id,
    validate_boot_backup_filename,
    validate_build_id,
    validate_kernel_version,
)


class TestValidator(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
