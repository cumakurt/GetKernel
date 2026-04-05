"""Install kernel DEB packages with optional backup metadata."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.exceptions import InstallationError
from utils.helpers import is_root, sudo_prefix

from modules.grub_manager import GrubManager


class Installer:
    """dpkg + apt-get install -f + initramfs + grub."""

    BACKUP_DIR = Path("/var/backups/getkernel")

    def __init__(self) -> None:
        self.backup_dir = self.BACKUP_DIR
        if is_root():
            self.backup_dir.mkdir(parents=True, exist_ok=True)

    def request_installation_approval(
        self,
        packages: List[Path],
        assume_yes: bool = False,
        *,
        default_confirm: bool = True,
    ) -> bool:
        if assume_yes:
            return True
        from utils.ui import confirm

        print("Packages to install:")
        for p in packages:
            try:
                size_mib = p.stat().st_size // (1024 * 1024)
            except OSError:
                size_mib = 0
            print(f"  - {p} ({size_mib} MiB)")
        return confirm(
            "Install these packages on this system now?",
            default=default_confirm,
        )

    def install_packages(
        self,
        package_list: List[Path],
        fix_dependencies: bool = True,
        kernel_version_hint: Optional[str] = None,
    ) -> Tuple[bool, str]:
        if not package_list:
            return False, "no packages"
        if not is_root() and not sudo_prefix():
            raise InstallationError("Root or sudo required for dpkg.")

        pre = sudo_prefix()
        files = [str(p) for p in package_list]
        cp = subprocess.run(
            pre + ["dpkg", "-i", *files],
            capture_output=True,
            text=True,
            timeout=3600,
        )
        log = (cp.stdout or "") + (cp.stderr or "")
        ok = cp.returncode == 0
        if fix_dependencies:
            env = os.environ.copy()
            env["DEBIAN_FRONTEND"] = "noninteractive"
            fix = subprocess.run(
                pre + ["apt-get", "install", "-f", "-y", "-qq"],
                capture_output=True,
                text=True,
                env=env,
                timeout=3600,
            )
            log += (fix.stdout or "") + (fix.stderr or "")
            ok = fix.returncode == 0

        if kernel_version_hint:
            subprocess.run(
                pre + ["update-initramfs", "-u", "-k", kernel_version_hint],
                capture_output=True,
                text=True,
            )
        subprocess.run(pre + ["update-grub"], capture_output=True, text=True)
        return ok, log

    def create_backup(self, kernel_version: Optional[str] = None) -> Optional[str]:
        if not is_root():
            return None
        kv = kernel_version or os.uname().release
        bid = f"backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        dest = self.backup_dir / bid
        dest.mkdir(parents=True, exist_ok=True)
        meta: Dict[str, Any] = {"kernel": kv, "files": []}
        for name in (
            f"vmlinuz-{kv}",
            f"initrd.img-{kv}",
            f"System.map-{kv}",
            f"config-{kv}",
        ):
            src = Path("/boot") / name
            if src.is_file():
                shutil.copy2(src, dest / name)
                meta["files"].append(name)
        (dest / "manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return bid

    def rollback(self, backup_id: str) -> bool:
        src = self.backup_dir / backup_id
        if not src.is_dir():
            return False
        man = src / "manifest.json"
        if not man.is_file():
            return False
        meta = json.loads(man.read_text(encoding="utf-8"))
        pre = sudo_prefix()
        for f in meta.get("files", []):
            shutil.copy2(src / f, Path("/boot") / f)
        subprocess.run(pre + ["update-grub"], capture_output=True)
        return True

    def list_backups(self) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        if not self.backup_dir.is_dir():
            return out
        for d in sorted(self.backup_dir.iterdir()):
            if not d.is_dir():
                continue
            man = d / "manifest.json"
            if man.is_file():
                meta = json.loads(man.read_text(encoding="utf-8"))
                out.append(
                    {
                        "id": d.name,
                        "kernel_version": meta.get("kernel", ""),
                        "date": d.name.replace("backup-", ""),
                        "size": "",
                    }
                )
        return out

    def update_grub(self, set_default: Optional[str] = None) -> bool:
        gm = GrubManager()
        if set_default:
            gm.set_default_entry(kernel_version=set_default)
        return gm.update_grub()

    def verify_installation(self, kernel_version: str) -> Tuple[bool, List[str]]:
        issues: List[str] = []
        if not (Path("/boot") / f"vmlinuz-{kernel_version}").is_file():
            issues.append(f"missing /boot/vmlinuz-{kernel_version}")
        moddir = Path("/lib/modules") / kernel_version
        if not moddir.is_dir():
            issues.append(f"missing {moddir}")
        return len(issues) == 0, issues

    def set_default_kernel(self, kernel_version: str) -> bool:
        return GrubManager().set_default_entry(kernel_version=kernel_version)

    def list_installed_kernels(self) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        boot = Path("/boot")
        if not boot.is_dir():
            return out
        current = os.uname().release
        for p in sorted(boot.glob("vmlinuz-*")):
            ver = p.name.replace("vmlinuz-", "")
            out.append(
                {
                    "version": ver,
                    "image": str(p),
                    "size": str(p.stat().st_size),
                    "install_date": "",
                    "is_running": str(ver == current),
                    "is_default": "",
                }
            )
        return out

    def remove_old_kernels(self, keep_count: int = 2, dry_run: bool = False) -> List[str]:
        """Remove old kernel packages, keeping the running kernel and the newest *keep_count*."""
        current = os.uname().release
        installed = self.list_installed_kernels()
        # Never remove the running kernel
        candidates = [k for k in installed if k["version"] != current]
        # Keep the newest keep_count kernels (by sort order)
        if len(candidates) <= keep_count:
            return []
        to_remove = candidates[:-keep_count] if keep_count > 0 else candidates
        removed: List[str] = []
        for k in to_remove:
            ver = k["version"]
            if dry_run:
                removed.append(f"[dry-run] would remove linux-image-{ver}")
                continue
            pre = sudo_prefix()
            pkgs = [f"linux-image-{ver}", f"linux-headers-{ver}"]
            cp = subprocess.run(
                pre + ["dpkg", "--purge", *pkgs],
                capture_output=True,
                text=True,
                timeout=600,
            )
            if cp.returncode == 0:
                removed.append(ver)
        return removed
