"""Tests for system_checker."""

import unittest

from modules.system_checker import SystemChecker


class TestSystemChecker(unittest.TestCase):
    def setUp(self) -> None:
        self.checker = SystemChecker()

    def test_is_debian_based(self) -> None:
        result = self.checker.is_debian_based()
        self.assertIsInstance(result, bool)

    def test_check_disk_space(self) -> None:
        ok, gb = self.checker.check_disk_space(required_gb=1)
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(gb, float)
        self.assertGreater(gb, 0.0)

    def test_check_memory(self) -> None:
        ok, ram_gb, swap_gb = self.checker.check_memory(required_gb=1)
        self.assertIsInstance(ok, bool)
        self.assertGreater(ram_gb, 0.0)
        self.assertGreaterEqual(swap_gb, 0.0)

    def test_get_current_kernel_version(self) -> None:
        version = self.checker.get_current_kernel_version()
        self.assertIsInstance(version, str)
        self.assertTrue(len(version) > 0)

    def test_validate_environment(self) -> None:
        vr = self.checker.validate_environment()
        self.assertIsInstance(vr.is_valid, bool)
        self.assertIsInstance(vr.errors, list)


if __name__ == "__main__":
    unittest.main()
