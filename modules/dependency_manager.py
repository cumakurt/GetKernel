"""APT/dpkg dependency checks and installation."""

from __future__ import annotations

import os
import subprocess
from typing import Dict, List, Tuple

from utils.constants import OPTIONAL_PACKAGES, REQUIRED_PACKAGES
from utils.exceptions import DependencyError
from utils.helpers import is_root, run_cmd, sudo_prefix


def _dpkg_installed(name: str) -> bool:
    cp = run_cmd(["dpkg", "-s", name])
    if cp.returncode != 0:
        return False
    return "install ok installed" in cp.stdout


def _installed_package_names(names: List[str]) -> set[str]:
    """Query many package states in one dpkg process for a faster startup check."""
    if not names:
        return set()
    cp = run_cmd(
        ["dpkg-query", "-W", "-f=${Package}\t${Status}\n", *names],
        timeout=60,
    )
    installed: set[str] = set()
    # dpkg-query returns non-zero when any requested package is absent, while
    # still printing valid rows for packages that are installed.
    for line in cp.stdout.splitlines():
        package, separator, status = line.partition("\t")
        if separator and status.strip() == "install ok installed":
            installed.add(package.strip())
    return installed


class DependencyManager:
    """Install build dependencies via apt."""

    def __init__(self, auto_install: bool = False) -> None:
        self.auto_install = auto_install

    def check_dependencies(self) -> Dict[str, bool]:
        packages = [*REQUIRED_PACKAGES, *OPTIONAL_PACKAGES]
        installed = _installed_package_names(packages)
        return {package: package in installed for package in packages}

    def get_missing_packages(self, include_optional: bool = False) -> List[str]:
        packages = list(REQUIRED_PACKAGES)
        if include_optional:
            packages.extend(OPTIONAL_PACKAGES)
        installed = _installed_package_names(packages)
        return [package for package in packages if package not in installed]

    def install_package(self, package_name: str) -> bool:
        return self._apt_install([package_name])[0]

    def install_all_dependencies(
        self,
        show_progress: bool = True,
        include_optional: bool = False,
    ) -> Tuple[bool, List[str]]:
        missing = self.get_missing_packages(include_optional=include_optional)
        if not missing:
            return True, []
        return self._apt_install(missing)

    def _apt_install(self, packages: List[str]) -> Tuple[bool, List[str]]:
        if not packages:
            return True, []
        if not is_root() and not sudo_prefix():
            raise DependencyError("Cannot install packages: need root or sudo.")
        cmd = sudo_prefix() + ["apt-get", "install", "-y", "-qq", *packages]
        env = os.environ.copy()
        env["DEBIAN_FRONTEND"] = "noninteractive"
        try:
            cp = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3600,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return False, packages
        except OSError as exc:
            raise DependencyError(f"Cannot start apt-get: {exc}") from exc
        if cp.returncode != 0:
            return False, packages
        installed = _installed_package_names(packages)
        failed = [package for package in packages if package not in installed]
        return len(failed) == 0, failed

    def update_package_cache(self) -> bool:
        cmd = sudo_prefix() + ["apt-get", "update", "-qq"]
        cp = run_cmd(cmd, timeout=600)
        return cp.returncode == 0

    def check_package_version(self, package_name: str, required_version: str) -> bool:
        info = self.get_package_info(package_name)
        if info["installed"] != "True" or not info["version"]:
            return False
        return (
            run_cmd(
                ["dpkg", "--compare-versions", info["version"], "ge", required_version],
                timeout=60,
            ).returncode
            == 0
        )

    def get_package_info(self, package_name: str) -> Dict[str, str]:
        cp = run_cmd(["dpkg-query", "-W", "-f=${Package}\t${Version}\t${Status}", package_name])
        installed = False
        version = ""
        if cp.returncode == 0 and cp.stdout:
            parts = cp.stdout.strip().split("\t")
            if len(parts) >= 2:
                version = parts[1]
            installed = len(parts) >= 3 and parts[2].strip() == "install ok installed"
        return {
            "name": package_name,
            "version": version,
            "installed": str(installed),
        }

    def estimate_download_size(self, packages: List[str]) -> int:
        if not packages:
            return 0
        cp = run_cmd(["apt-get", "install", "-y", "--print-uris", *packages])
        if cp.returncode != 0:
            return 0
        total = 0
        for line in cp.stdout.splitlines():
            parts = line.split()
            uri = parts[0].strip("'\"") if parts else ""
            if len(parts) >= 3 and uri.startswith(("http://", "https://")):
                try:
                    total += int(parts[2])
                except ValueError:
                    continue
        return total

    def verify_installation(self, package_name: str) -> bool:
        return _dpkg_installed(package_name)
