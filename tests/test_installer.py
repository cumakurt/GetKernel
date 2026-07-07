"""Tests for installer security and cleanup logic."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.installer import Installer


class TestInstaller(unittest.TestCase):
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
            with patch.object(Installer, "BACKUP_DIR", backup_root):
                self.assertFalse(inst.rollback(bid))

    def test_remove_old_kernels_rejects_negative_keep(self) -> None:
        inst = Installer()
        with self.assertRaises(ValueError):
            inst.remove_old_kernels(keep_count=-1)


if __name__ == "__main__":
    unittest.main()
