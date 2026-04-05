"""OS and hardware validation."""

from __future__ import annotations

import os
import platform
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import psutil
from packaging.version import parse as parse_version

from utils.constants import DEBIAN_BASED_IDS, REQUIRED_COMMANDS
from utils.helpers import is_root, project_root, run_cmd, which


@dataclass
class ValidationResult:
    """Aggregated environment check result."""

    is_valid: bool
    errors: List[str]
    warnings: List[str]
    recommendations: List[str]
    system_info: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "recommendations": self.recommendations,
            "system_info": self.system_info,
        }


class SystemChecker:
    """Check Debian-based host and resources."""

    MIN_REQUIREMENTS = {
        "disk_gb": 20,
        "ram_gb": 4,
        "python_version": "3.8",
        "gcc_version": "9.0",
    }

    def __init__(self) -> None:
        self.os_info = self._detect_os()
        self.hardware_info = self._detect_hardware()

    def is_debian_based(self) -> bool:
        if os.path.isfile("/etc/debian_version"):
            return True
        os_release = self._read_os_release()
        id_like = os_release.get("id_like", "").lower()
        cid = os_release.get("id", "").lower()
        if cid in DEBIAN_BASED_IDS:
            return True
        for token in id_like.split():
            if token in DEBIAN_BASED_IDS:
                return True
        return False

    def check_disk_space(
        self, required_gb: int = 20, path: str | None = None
    ) -> Tuple[bool, float]:
        root = project_root()
        check = path or str(root / "data" / "builds")
        p = check
        while p and not os.path.isdir(p):
            p = os.path.dirname(p)
        if not p:
            p = "/"
        usage = psutil.disk_usage(p)
        free_gb = usage.free / (1024**3)
        return free_gb >= required_gb, free_gb

    def check_memory(self, required_gb: int = 4) -> Tuple[bool, float, float]:
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        ram_gb = vm.total / (1024**3)
        swap_gb = swap.total / (1024**3)
        return ram_gb + swap_gb * 0.5 >= required_gb, ram_gb, swap_gb

    def check_root_privileges(self) -> bool:
        return is_root() or which("sudo") is not None

    def get_current_kernel_version(self) -> str:
        return os.uname().release

    def read_cpu_model_from_proc(self) -> str:
        """Best-effort CPU marketing name on Linux (/proc/cpuinfo)."""
        try:
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith("model name") or line.startswith("Model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
        return ""

    def get_loaded_kernel_modules(
        self, max_display: Optional[int] = 80
    ) -> Tuple[List[str], int]:
        """Return (module names, total count). If max_display is None, return all modules."""
        path = "/proc/modules"
        if not os.path.isfile(path):
            return [], 0
        names: List[str] = []
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    parts = line.split()
                    if parts:
                        names.append(parts[0])
        except OSError:
            return [], 0
        total = len(names)
        if max_display is None:
            return names, total
        return names[:max_display], total

    def get_pci_summary_lines(self, max_lines: int = 14) -> List[str]:
        """Short lspci lines for GPU, audio, network, NVMe (empty if unavailable)."""
        lspci = which("lspci")
        if not lspci:
            return []
        cp = run_cmd([lspci, "-nn"], timeout=12)
        if cp.returncode != 0 or not (cp.stdout or "").strip():
            return []
        out: List[str] = []
        for line in cp.stdout.splitlines():
            s = line.strip()
            if not s:
                continue
            low = s.lower()
            if re.search(
                r"vga|3d|display controller|audio device|ethernet|network controller|"
                r"wireless|nvme|non-volatile memory|infiniband|scsi storage",
                low,
            ):
                out.append(s[:118] + ("…" if len(s) > 118 else ""))
            if len(out) >= max_lines:
                break
        return out

    def get_interactive_snapshot(self) -> Dict[str, Any]:
        """Data for the interactive wizard: OS/kernel, hardware, modules, PCI hints."""
        info = self.get_system_info()
        cpu_name = self.read_cpu_model_from_proc()
        if cpu_name:
            info["hardware"]["cpu"]["model"] = cpu_name
        mods, mod_total = self.get_loaded_kernel_modules(None)
        pci = self.get_pci_summary_lines(16)
        host = platform.node() or ""
        return {
            "info": info,
            "hostname": host,
            "loaded_modules": mods,
            "loaded_modules_total": mod_total,
            "pci_summary": pci,
        }

    def get_cpu_info(self) -> Dict[str, Any]:
        phys = psutil.cpu_count(logical=False) or 1
        log = psutil.cpu_count(logical=True) or phys
        freq = psutil.cpu_freq()
        return {
            "cores": phys,
            "threads": log,
            "model": platform.processor() or "unknown",
            "architecture": platform.machine(),
            "max_frequency": freq.max if freq else 0.0,
        }

    def check_compiler_version(self) -> Tuple[bool, str]:
        gcc = which("gcc")
        if not gcc:
            return False, ""
        cp = run_cmd(["gcc", "-dumpversion"])
        if cp.returncode != 0:
            return False, ""
        ver = cp.stdout.strip()
        try:
            ok = parse_version(str(ver)) >= parse_version(str(self.MIN_REQUIREMENTS["gcc_version"]))
        except Exception:
            ok = True
        return ok, ver

    def get_system_info(self) -> Dict[str, Any]:
        gcc_ok, gcc_ver = self.check_compiler_version()
        make_v = ""
        mk = which("make")
        if mk:
            m = run_cmd(["make", "--version"])
            if m.returncode == 0:
                make_v = m.stdout.splitlines()[0][:80]
        disk_ok, disk_free = self.check_disk_space()
        mem_ok, ram_gb, swap_gb = self.check_memory()
        return {
            "os": {
                "name": self.os_info.get("name", ""),
                "version": self.os_info.get("version", ""),
                "codename": self.os_info.get("codename", ""),
                "kernel": self.get_current_kernel_version(),
            },
            "hardware": {
                "cpu": self.get_cpu_info(),
                "memory": {
                    "ram_gb": ram_gb,
                    "swap_gb": swap_gb,
                    "ram_ok": mem_ok,
                },
                "disk": {"free_gb": disk_free, "ok": disk_ok},
            },
            "compiler": {"gcc": gcc_ver, "gcc_ok": gcc_ok, "make": make_v},
        }

    def validate_environment(self) -> ValidationResult:
        errors: List[str] = []
        warnings: List[str] = []
        rec: List[str] = []
        info = self.get_system_info()

        if not self.is_debian_based():
            errors.append("This host does not appear to be Debian-based.")

        if not self.check_root_privileges():
            errors.append("Root or sudo is required for installs and builds in system paths.")

        disk_ok, free = self.check_disk_space(int(str(self.MIN_REQUIREMENTS["disk_gb"])))
        if not disk_ok:
            errors.append(
                f"Insufficient disk space: {free:.1f} GB free; "
                f"{self.MIN_REQUIREMENTS['disk_gb']} GB recommended."
            )

        mem_ok, ram_gb, swap_gb = self.check_memory(int(str(self.MIN_REQUIREMENTS["ram_gb"])))
        if not mem_ok:
            warnings.append(
                f"Low RAM ({ram_gb:.1f} GB); swap {swap_gb:.1f} GB — build may be slow or fail."
            )
            rec.append("Close other apps or add swap before compiling.")

        gcc_ok, gcc_ver = self.check_compiler_version()
        if not gcc_ok and gcc_ver:
            warnings.append(f"GCC {gcc_ver} may be older than recommended.")

        for cmd in REQUIRED_COMMANDS:
            if not which(cmd):
                errors.append(f"Required command not found in PATH: {cmd}")

        valid = len(errors) == 0
        return ValidationResult(
            is_valid=valid,
            errors=errors,
            warnings=warnings,
            recommendations=rec,
            system_info=info,
        )

    def _detect_os(self) -> Dict[str, str]:
        data = self._read_os_release()
        return {
            "name": data.get("name", data.get("id", "")),
            "version": data.get("version_id", ""),
            "codename": data.get("version_codename", ""),
        }

    def _read_os_release(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for path in ("/etc/os-release", "/usr/lib/os-release"):
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    v = re.sub(r'^"|"$', "", v.strip())
                    out[k.strip().lower()] = v
            break
        return out

    def _detect_hardware(self) -> Dict[str, Any]:
        return {"cpu": self.get_cpu_info()}

