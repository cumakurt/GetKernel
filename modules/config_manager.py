"""Kernel .config preparation from running system."""

from __future__ import annotations

import gzip
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from utils.exceptions import ConfigError
from utils.helpers import run_cmd


class ConfigManager:
    """Manage kernel Kconfig for a source tree."""

    CRITICAL_OPTIONS = [
        "CONFIG_MODULES=y",
        "CONFIG_MODULE_UNLOAD=y",
        "CONFIG_PRINTK=y",
    ]

    def __init__(self, kernel_source_dir: str) -> None:
        self.kernel_source_dir = Path(kernel_source_dir)
        self.config_file = self.kernel_source_dir / ".config"

    def get_current_config(self) -> str:
        release = os.uname().release
        candidates = [
            Path(f"/boot/config-{release}"),
            Path("/proc/config.gz"),
        ]
        for c in candidates:
            if not c.exists():
                continue
            if c.name == "config.gz":
                with gzip.open(c, "rb") as f:
                    return f.read().decode("utf-8", errors="replace")
            return c.read_text(encoding="utf-8", errors="replace")
        raise ConfigError("No running kernel config found (/boot/config-* or /proc/config.gz).")

    def extract_active_modules(self) -> Set[str]:
        out: Set[str] = set()
        cp = run_cmd(["lsmod"])
        if cp.returncode != 0:
            return out
        for line in cp.stdout.splitlines()[1:]:
            parts = line.split()
            if parts:
                out.add(parts[0])
        return out

    def get_module_dependencies(self, module_name: str) -> List[str]:
        mod = Path(f"/sys/module/{module_name}/holders")
        if not mod.is_dir():
            return []
        return [p.name for p in mod.iterdir()]

    def create_new_config(
        self,
        base_config: str,
        enable_modules: Optional[List[str]] = None,
    ) -> bool:
        self.kernel_source_dir.mkdir(parents=True, exist_ok=True)
        self.config_file.write_text(base_config, encoding="utf-8")
        ok = self.run_oldconfig(interactive=False)
        if ok and enable_modules:
            for mod in enable_modules:
                self.enable_module(mod)
        return ok

    def run_oldconfig(self, interactive: bool = False) -> bool:
        if not self.config_file.is_file():
            raise ConfigError(".config missing")
        target = "oldconfig" if interactive else "olddefconfig"
        env = os.environ.copy()
        env.setdefault("TERM", "xterm")
        cp = subprocess.run(
            ["make", target],
            cwd=self.kernel_source_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=3600,
        )
        if cp.returncode != 0:
            raise ConfigError(f"make {target} failed: {cp.stderr[-2000:]}")
        return True

    def merge_config_fragments(self, fragment_paths: List[Path]) -> None:
        """
        Merge Kconfig fragment files into the existing .config using the kernel's
        scripts/kconfig/merge_config.sh (-m: merge only), then run olddefconfig.
        """
        if not fragment_paths:
            return
        if not self.config_file.is_file():
            raise ConfigError(".config missing; create base config before merging fragments")
        merge_script = self.kernel_source_dir / "scripts" / "kconfig" / "merge_config.sh"
        if not merge_script.is_file():
            raise ConfigError(
                "scripts/kconfig/merge_config.sh not found (incomplete or too old kernel tree)."
            )
        args = ["bash", str(merge_script), "-m"]
        for p in fragment_paths:
            rp = Path(p).resolve()
            if not rp.is_file():
                raise ConfigError(f"Config fragment not found: {rp}")
            args.append(str(rp))
        cp = subprocess.run(
            args,
            cwd=self.kernel_source_dir,
            capture_output=True,
            text=True,
            timeout=3600,
        )
        if cp.returncode != 0:
            err = (cp.stderr or "") + (cp.stdout or "")
            raise ConfigError(f"merge_config.sh failed:\n{err[-4000:]}")
        self.run_oldconfig(interactive=False)

    def run_localmodconfig(self) -> None:
        """Run ``make localmodconfig`` to trim .config to loaded modules (running system)."""
        if not self.config_file.is_file():
            raise ConfigError(".config missing")
        env = os.environ.copy()
        env.setdefault("TERM", "xterm")
        cp = subprocess.run(
            ["make", "localmodconfig"],
            cwd=self.kernel_source_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=3600,
        )
        if cp.returncode != 0:
            raise ConfigError(f"make localmodconfig failed: {(cp.stderr or '')[-4000:]}")

    def run_menuconfig(self) -> None:
        """Run interactive ``make menuconfig`` (requires a TTY)."""
        if not self.config_file.is_file():
            raise ConfigError(".config missing")
        env = os.environ.copy()
        env.setdefault("TERM", "xterm")
        cp = subprocess.run(
            ["make", "menuconfig"],
            cwd=self.kernel_source_dir,
            env=env,
            timeout=86400,
        )
        if cp.returncode != 0:
            raise ConfigError("make menuconfig failed or was cancelled")

    def apply_build_profile(self, profile_path: Path) -> None:
        """Merge a named build profile fragment into the current .config."""
        self.merge_config_fragments([profile_path])

    def validate_config(self) -> Tuple[bool, List[str]]:
        errors: List[str] = []
        if not self.config_file.is_file():
            return False, ["missing .config"]
        text = self.config_file.read_text(encoding="utf-8", errors="replace")
        for opt in self.CRITICAL_OPTIONS:
            key = opt.split("=")[0]
            if key not in text:
                errors.append(f"missing {key}")
        return len(errors) == 0, errors

    def enable_module(self, module_name: str) -> bool:
        # module_name like EXT4_FS
        key = module_name if module_name.startswith("CONFIG_") else f"CONFIG_{module_name}"
        text = self.config_file.read_text(encoding="utf-8")
        pattern = re.compile(rf"^# {re.escape(key)} is not set", re.MULTILINE)
        if pattern.search(text):
            text = pattern.sub(f"{key}=m", text)
            self.config_file.write_text(text, encoding="utf-8")
            return True
        pat2 = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
        if pat2.search(text):
            text = pat2.sub(f"{key}=m", text)
            self.config_file.write_text(text, encoding="utf-8")
            return True
        return False

    def disable_module(self, module_name: str) -> bool:
        key = module_name if module_name.startswith("CONFIG_") else f"CONFIG_{module_name}"
        text = self.config_file.read_text(encoding="utf-8")
        pat = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
        if pat.search(text):
            new = pat.sub(f"# {key} is not set", text)
            self.config_file.write_text(new, encoding="utf-8")
            return True
        return False

    def get_config_diff(self, old_config: str, new_config: str) -> Dict[str, List]:
        old_lines = set(old_config.splitlines())
        new_lines = set(new_config.splitlines())
        return {
            "added": sorted(new_lines - old_lines),
            "removed": sorted(old_lines - new_lines),
            "changed": [],
        }

    def optimize_config(self, optimization_level: str = "balanced") -> bool:
        """Apply common optimisation presets to the current .config.

        Levels:
          * ``performance`` — favour speed (no kernel debug, O2).
          * ``size``        — minimise image (optimise for size, strip debug).
          * ``balanced``    — reasonable defaults for desktop / server.
        """
        if not self.config_file.is_file():
            return False

        text = self.config_file.read_text(encoding="utf-8")
        changes: Dict[str, str] = {}

        if optimization_level == "performance":
            changes.update({
                "CONFIG_CC_OPTIMIZE_FOR_PERFORMANCE": "y",
                "CONFIG_CC_OPTIMIZE_FOR_SIZE": "n",
                "CONFIG_DEBUG_INFO_NONE": "y",
                "CONFIG_PREEMPT_VOLUNTARY": "y",
            })
        elif optimization_level == "size":
            changes.update({
                "CONFIG_CC_OPTIMIZE_FOR_SIZE": "y",
                "CONFIG_CC_OPTIMIZE_FOR_PERFORMANCE": "n",
                "CONFIG_DEBUG_INFO_NONE": "y",
                "CONFIG_MODULES": "y",
            })
        else:  # balanced
            changes.update({
                "CONFIG_CC_OPTIMIZE_FOR_PERFORMANCE": "y",
                "CONFIG_CC_OPTIMIZE_FOR_SIZE": "n",
                "CONFIG_MODULES": "y",
                "CONFIG_MODULE_UNLOAD": "y",
            })

        for key, val in changes.items():
            pat_unset = re.compile(rf"^# {re.escape(key)} is not set", re.MULTILINE)
            pat_set = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
            new_line = f"{key}={val}"
            if val == "n":
                new_line = f"# {key} is not set"

            if pat_set.search(text):
                text = pat_set.sub(new_line, text)
            elif pat_unset.search(text):
                text = pat_unset.sub(new_line, text)
            # If neither pattern found, key is not present — skip it

        self.config_file.write_text(text, encoding="utf-8")
        return self.run_oldconfig(interactive=False)

    def save_config(self, output_path: Optional[str] = None) -> str:
        dest = Path(output_path) if output_path else self.config_file
        if dest != self.config_file:
            dest.write_text(self.config_file.read_text(encoding="utf-8"), encoding="utf-8")
        return str(dest)
