"""Unit tests for utils.helpers."""

import unittest

from utils.helpers import assume_yes_from_env, needs_elevation


class TestNeedsElevation(unittest.TestCase):
    def test_help_and_version_skip_elevation(self) -> None:
        self.assertFalse(needs_elevation(["--help"]))
        self.assertFalse(needs_elevation(["-h"]))
        self.assertFalse(needs_elevation(["--version"]))

    def test_check_and_list_skip(self) -> None:
        self.assertFalse(needs_elevation(["check"]))
        self.assertFalse(needs_elevation(["list"]))
        self.assertFalse(needs_elevation(["about"]))

    def test_deps_without_install_skips(self) -> None:
        self.assertFalse(needs_elevation(["deps"]))

    def test_deps_install_needs(self) -> None:
        self.assertTrue(needs_elevation(["deps", "--install"]))

    def test_build_needs(self) -> None:
        self.assertTrue(needs_elevation(["build", "--version", "6.12.0"]))

    def test_prepare_needs(self) -> None:
        self.assertTrue(needs_elevation(["prepare", "--version", "6.12.0"]))

    def test_empty_argv_needs(self) -> None:
        self.assertTrue(needs_elevation([]))


class TestAssumeYesEnv(unittest.TestCase):
    def test_default_false(self) -> None:
        import os

        os.environ.pop("GETKERNEL_ASSUME_YES", None)
        self.assertFalse(assume_yes_from_env())

    def test_truthy(self) -> None:
        import os

        os.environ["GETKERNEL_ASSUME_YES"] = "1"
        try:
            self.assertTrue(assume_yes_from_env())
        finally:
            os.environ.pop("GETKERNEL_ASSUME_YES", None)


if __name__ == "__main__":
    unittest.main()
