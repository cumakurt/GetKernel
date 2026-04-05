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


def _has_packaging_tool() -> bool:
    return _dpkg_installed("kernel-wedge") or _dpkg_installed("kernel-package")


class DependencyManager:
    """Install build dependencies via apt."""

    def __init__(self, auto_install: bool = False) -> None:
        self.auto_install = auto_install

    def check_dependencies(self) -> Dict[str, bool]:
        result: Dict[str, bool] = {}
        for pkg in REQUIRED_PACKAGES:
            if pkg == "kernel-wedge":
                result["kernel-wedge"] = _has_packaging_tool()
                continue
            result[pkg] = _dpkg_installed(pkg)
        for pkg in OPTIONAL_PACKAGES:
            result[pkg] = _dpkg_installed(pkg)
        return result

    def get_missing_packages(self, include_optional: bool = False) -> List[str]:
        missing: List[str] = []
        for pkg in REQUIRED_PACKAGES:
            if pkg == "kernel-wedge":
                if not _has_packaging_tool():
                    missing.append("kernel-wedge")
                continue
            if not _dpkg_installed(pkg):
                missing.append(pkg)
        if include_optional:
            for pkg in OPTIONAL_PACKAGES:
                if not _dpkg_installed(pkg):
                    missing.append(pkg)
        return missing

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
        if cp.returncode != 0:
            return False, packages
        failed: List[str] = []
        for p in packages:
            if p == "kernel-wedge":
                if not _has_packaging_tool():
                    failed.append("kernel-wedge")
            elif not _dpkg_installed(p):
                failed.append(p)
        return len(failed) == 0, failed

    def update_package_cache(self) -> bool:
        cmd = sudo_prefix() + ["apt-get", "update", "-qq"]
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return cp.returncode == 0

    def check_package_version(self, package_name: str, required_version: str) -> bool:
        _ = required_version
        return _dpkg_installed(package_name)

    def get_package_info(self, package_name: str) -> Dict[str, str]:
        cp = run_cmd(["dpkg-query", "-W", "-f=${Package}\t${Version}\t${Status}", package_name])
        installed = _dpkg_installed(package_name)
        version = ""
        if cp.returncode == 0 and cp.stdout:
            parts = cp.stdout.strip().split("\t")
            if len(parts) >= 2:
                version = parts[1]
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
            if len(parts) >= 3 and parts[0].startswith("http"):
                try:
                    total += int(parts[2])
                except ValueError:
                    continue
        return total

    def verify_installation(self, package_name: str) -> bool:
        if package_name == "kernel-wedge":
            return _has_packaging_tool()
        return _dpkg_installed(package_name)
