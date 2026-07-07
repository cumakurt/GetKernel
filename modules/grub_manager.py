"""GRUB configuration helpers."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from utils.helpers import is_root, sudo_prefix


class GrubManager:
    """Read and update /etc/default/grub; run update-grub."""

    GRUB_CONFIG = Path("/etc/default/grub")
    GRUB_CFG = Path("/boot/grub/grub.cfg")

    def __init__(self) -> None:
        self.config_file = self.GRUB_CONFIG
        self.grub_cfg = self.GRUB_CFG

    def get_menu_entries(self) -> List[Dict[str, str]]:
        if not self.grub_cfg.is_file():
            return []
        try:
            text = self.grub_cfg.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        entries: List[Dict[str, str]] = []
        idx = 0
        for m in re.finditer(r"menuentry\s+'([^']+)'", text):
            title = m.group(1)
            kver = ""
            km = re.search(r"Linux ([^\s]+)", title)
            if km:
                kver = km.group(1)
            entries.append(
                {
                    "index": str(idx),
                    "title": title,
                    "kernel": kver,
                    "is_default": "false",
                }
            )
            idx += 1
        return entries

    def get_default_entry(self) -> Optional[str]:
        if not self.config_file.is_file():
            return None
        for line in self.config_file.read_text().splitlines():
            if line.strip().startswith("GRUB_DEFAULT="):
                return line.split("=", 1)[1].strip().strip('"')
        return None

    def set_default_entry(
        self,
        kernel_version: Optional[str] = None,
        menu_index: Optional[int] = None,
    ) -> bool:
        if not self.config_file.is_file():
            return False
        if menu_index is not None:
            val = f'"{menu_index}"'
        elif kernel_version:
            val = f'"1>{kernel_version}"'
        else:
            return False
        text = self.config_file.read_text(encoding="utf-8")
        if "GRUB_DEFAULT=" in text:
            text = re.sub(
                r"^GRUB_DEFAULT=.*$",
                f"GRUB_DEFAULT={val}",
                text,
                flags=re.MULTILINE,
            )
        else:
            text += f"\nGRUB_DEFAULT={val}\n"
        cmd = sudo_prefix()
        if cmd:
            # Use a secure temp file to avoid symlink attacks
            with tempfile.NamedTemporaryFile(
                mode="w", suffix="-grub", prefix="getkernel-", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(text)
                tmp_path = tmp.name
            try:
                r = subprocess.run(cmd + ["cp", tmp_path, str(self.config_file)])
                return r.returncode == 0
            finally:
                Path(tmp_path).unlink(missing_ok=True)
        self.config_file.write_text(text, encoding="utf-8")
        return True

    def update_grub(self) -> bool:
        if not is_root() and not sudo_prefix():
            return False
        cmd = sudo_prefix() + ["update-grub"]
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return cp.returncode == 0

    def set_timeout(self, seconds: int) -> bool:
        if not self.config_file.is_file():
            return False
        text = self.config_file.read_text(encoding="utf-8")
        line = f"GRUB_TIMEOUT={seconds}"
        if re.search(r"^GRUB_TIMEOUT=", text, re.MULTILINE):
            text = re.sub(r"^GRUB_TIMEOUT=.*$", line, text, flags=re.MULTILINE)
        else:
            text += "\n" + line + "\n"
        if is_root():
            self.config_file.write_text(text, encoding="utf-8")
            return True
        return False

    def add_kernel_parameter(self, parameter: str) -> bool:
        if not self.config_file.is_file():
            return False
        text = self.config_file.read_text(encoding="utf-8")
        m = re.search(r'^GRUB_CMDLINE_LINUX_DEFAULT="([^"]*)"', text, re.MULTILINE)
        if not m:
            return False
        cur = m.group(1)
        if parameter in cur:
            return True
        new = f'{cur} {parameter}'.strip()
        text = re.sub(
            r'^GRUB_CMDLINE_LINUX_DEFAULT="[^"]*"',
            f'GRUB_CMDLINE_LINUX_DEFAULT="{new}"',
            text,
            flags=re.MULTILINE,
        )
        self.config_file.write_text(text, encoding="utf-8")
        return is_root()

    def remove_kernel_parameter(self, parameter: str) -> bool:
        if not self.config_file.is_file():
            return False
        text = self.config_file.read_text(encoding="utf-8")
        m = re.search(r'^GRUB_CMDLINE_LINUX_DEFAULT="([^"]*)"', text, re.MULTILINE)
        if not m:
            return False
        parts = [p for p in m.group(1).split() if p != parameter]
        new = " ".join(parts)
        text = re.sub(
            r'^GRUB_CMDLINE_LINUX_DEFAULT="[^"]*"',
            f'GRUB_CMDLINE_LINUX_DEFAULT="{new}"',
            text,
            flags=re.MULTILINE,
        )
        self.config_file.write_text(text, encoding="utf-8")
        return is_root()

    def backup_config(self) -> Path:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = Path("/var/backups/getkernel") / f"grub-{ts}.cfg"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.config_file, dest)
        return dest

    def restore_config(self, backup_file: Path) -> bool:
        if not backup_file.is_file():
            return False
        shutil.copy2(backup_file, self.config_file)
        return True
