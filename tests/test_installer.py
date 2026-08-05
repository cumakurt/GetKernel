"""Tests for installer security and cleanup logic."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.installer import Installer


class TestInstaller(unittest.TestCase):
    def test_select_runtime_packages_excludes_libc_and_debug(self) -> None:
        packages = [
            Path("linux-image.deb"),
            Path("linux-headers.deb"),
            Path("linux-libc-dev.deb"),
            Path("linux-debug.deb"),
        ]
        names = {
            "linux-image.deb": "linux-image-6.12.8",
            "linux-headers.deb": "linux-headers-6.12.8",
            "linux-libc-dev.deb": "linux-libc-dev",
            "linux-debug.deb": "linux-image-6.12.8-dbg",
        }

        def package_field(args: list, **_kwargs: object) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(args, 0, names[Path(args[2]).name] + "\n", "")

        with patch("modules.installer.run_cmd", side_effect=package_field):
            selected = Installer.select_runtime_packages(packages)
        self.assertEqual(selected, packages[:2])

    def test_kernel_sort_key_orders_numeric_versions(self) -> None:
        keys = [
            Installer._kernel_sort_key("6.9.0-getkernel"),
            Installer._kernel_sort_key("6.10.0-getkernel"),
            Installer._kernel_sort_key("6.8.12-getkernel"),
        ]
        self.assertLess(keys[2], keys[0])
        self.assertLess(keys[0], keys[1])

    def test_rollback_rejects_invalid_backup_id(self) -> None:
        inst = Installer()
        self.assertFalse(inst.rollback("../outside"))

    def test_rollback_rejects_traversal_in_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            backup_root = Path(td)
            bid = "backup-20260707-120000"
            src = backup_root / bid
            src.mkdir()
            (src / "manifest.json").write_text(
                json.dumps({"files": ["../../../etc/passwd"]}),
                encoding="utf-8",
            )
            inst = Installer()
            inst.backup_dir = backup_root
            with patch("modules.installer.is_root", return_value=True):
                self.assertFalse(inst.rollback(bid))

    def test_remove_old_kernels_rejects_negative_keep(self) -> None:
        inst = Installer()
        with self.assertRaises(ValueError):
            inst.remove_old_kernels(keep_count=-1)

    def test_find_linux_packages_matches_exact_release_only(self) -> None:
        output = "\n".join(
            (
                "linux-image-6.1.0",
                "linux-headers-6.1.0",
                "linux-modules-6.1.0",
                "linux-image-6.10.0",
                "linux-headers-6.1.0-other",
                "linux-image-6.1.0-dbg",
            )
        )
        with patch("modules.installer.run_cmd") as command:
            command.return_value = subprocess.CompletedProcess(
                ["dpkg-query"], 0, output, ""
            )
            packages = Installer().find_linux_packages("6.1.0")
        self.assertEqual(
            packages,
            [
                "linux-headers-6.1.0",
                "linux-image-6.1.0",
                "linux-image-6.1.0-dbg",
                "linux-modules-6.1.0",
            ],
        )

    def test_invalid_backup_manifest_is_ignored_when_listing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            backup_root = Path(td)
            broken = backup_root / "backup-20260707-120000"
            broken.mkdir()
            (broken / "manifest.json").write_text("{broken", encoding="utf-8")
            inst = Installer()
            inst.backup_dir = backup_root
            self.assertEqual(inst.list_backups(), [])

    def test_package_state_verification_detects_removed_package(self) -> None:
        with patch.object(Installer, "_package_name", return_value="linux-image-6.1.0"):
            with patch("modules.installer.run_cmd") as command:
                command.return_value = subprocess.CompletedProcess(
                    ["dpkg-query"], 1, "", "package is not installed"
                )
                issues = Installer._verify_package_states([Path("kernel.deb")])
        self.assertTrue(any("not installed" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
