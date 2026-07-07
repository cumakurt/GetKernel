"""Tests for system advisor warnings."""

import unittest
from unittest.mock import patch

from modules.system_advisor import collect_build_warnings, status_secure_boot


class TestSystemAdvisor(unittest.TestCase):
    @patch("modules.system_advisor._dkms_modules", return_value=["nvidia", "zfs"])
    @patch("modules.system_advisor._loaded_gpu_drivers", return_value=["nvidia"])
    @patch("modules.system_advisor._secure_boot_enabled", return_value=True)
    def test_collect_build_warnings(self, *_mocks) -> None:
        warnings = collect_build_warnings("6.13-rc1")
        self.assertTrue(any("DKMS" in w for w in warnings))
        self.assertTrue(any("Secure Boot" in w for w in warnings))
        self.assertTrue(any("Release candidate" in w for w in warnings))

    @patch("modules.system_advisor._secure_boot_enabled", return_value=False)
    def test_status_secure_boot(self, _mock: object) -> None:
        info = status_secure_boot()
        self.assertFalse(info["secure_boot"])


if __name__ == "__main__":
    unittest.main()
