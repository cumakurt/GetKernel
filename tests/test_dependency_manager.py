"""Dependency checks should batch dpkg queries and preserve requested order."""

import subprocess
import unittest
from unittest.mock import patch

from modules.dependency_manager import DependencyManager, _installed_package_names


class TestDependencyManager(unittest.TestCase):
    def test_bulk_query_uses_rows_even_when_some_packages_are_missing(self) -> None:
        output = "gcc\tinstall ok installed\nmake\tunknown ok not-installed\n"
        with patch("modules.dependency_manager.run_cmd") as command:
            command.return_value = subprocess.CompletedProcess(
                ["dpkg-query"], 1, output, "missing package"
            )
            installed = _installed_package_names(["gcc", "make", "flex"])

        self.assertEqual(installed, {"gcc"})
        self.assertEqual(command.call_count, 1)

    def test_package_info_uses_single_query(self) -> None:
        output = "gcc\t14.2.0\tinstall ok installed"
        with patch("modules.dependency_manager.run_cmd") as command:
            command.return_value = subprocess.CompletedProcess(
                ["dpkg-query"], 0, output, ""
            )
            info = DependencyManager().get_package_info("gcc")

        self.assertEqual(info["version"], "14.2.0")
        self.assertEqual(info["installed"], "True")
        self.assertEqual(command.call_count, 1)

    def test_estimate_download_size_parses_quoted_apt_uri(self) -> None:
        output = "'https://deb.example/gcc.deb' gcc.deb 123 SHA256:abc\n"
        with patch("modules.dependency_manager.run_cmd") as command:
            command.return_value = subprocess.CompletedProcess(
                ["apt-get"], 0, output, ""
            )
            size = DependencyManager().estimate_download_size(["gcc"])

        self.assertEqual(size, 123)


if __name__ == "__main__":
    unittest.main()
