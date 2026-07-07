"""Pre-build / pre-install advisory checks (DKMS, Secure Boot, drivers)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List

from utils.helpers import run_cmd


def _secure_boot_enabled() -> bool:
    if os.path.isdir("/sys/firmware/efi"):
        cp = run_cmd(["mokutil", "--sb-state"])
        if cp.returncode == 0 and "enabled" in cp.stdout.lower():
            return True
    return False


def _dkms_modules() -> List[str]:
    cp = run_cmd(["dkms", "status"])
    if cp.returncode != 0:
        return []
    modules: List[str] = []
    for line in cp.stdout.splitlines():
        m = re.match(r"^([^,/]+)", line.strip())
        if m:
            modules.append(m.group(1))
    return sorted(set(modules))


def _loaded_gpu_drivers() -> List[str]:
    cp = run_cmd(["lsmod"])
    if cp.returncode != 0:
        return []
    hits: List[str] = []
    for name in ("nvidia", "nouveau", "amdgpu", "i915"):
        if any(line.startswith(name) for line in cp.stdout.splitlines()[1:]):
            hits.append(name)
    return hits


def collect_build_warnings(kernel_version: str) -> List[str]:
    """Return human-readable warnings before starting a long build."""
    warnings: List[str] = []
    ver = kernel_version.lower()
    if "rc" in ver or "beta" in ver:
        warnings.append(
            "Release candidate or beta kernel selected: out-of-tree drivers "
            "(NVIDIA, DKMS) often fail on newer kernels."
        )
    dkms = _dkms_modules()
    if dkms:
        warnings.append(
            "DKMS modules registered: "
            + ", ".join(dkms[:8])
            + (" …" if len(dkms) > 8 else "")
            + ". Post-install may run module rebuilds; failures can leave dpkg in a bad state."
        )
    gpus = _loaded_gpu_drivers()
    if "nvidia" in gpus and ("rc" in ver or ver.startswith("6.") or ver.startswith("7.")):
        warnings.append(
            "Proprietary NVIDIA driver detected. Verify driver support for this kernel "
            "before installing (check vendor release notes)."
        )
    if _secure_boot_enabled():
        warnings.append(
            "Secure Boot appears enabled. Unsigned or self-signed kernel modules may "
            "require MOK enrollment or signing."
        )
    return warnings


def collect_install_warnings(kernel_version: str) -> List[str]:
    return collect_build_warnings(kernel_version)


def status_secure_boot() -> Dict[str, Any]:
    enabled = _secure_boot_enabled()
    return {
        "secure_boot": enabled,
        "note": "Enabled — module signing may be required" if enabled else "Not detected or disabled",
    }
