"""Install kernel DEB packages with optional backup metadata."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.exceptions import InstallationError
from utils.helpers import is_root, run_cmd, sudo_prefix
from utils.validator import (
    check_file_safety,
    path_is_within,
    validate_backup_id,
    validate_boot_backup_filename,
)

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
            "Install these newly built packages on this system now?",
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

        for deb in package_list:
            ok, msg = check_file_safety(deb)
            if not ok:
                raise InstallationError(f"Unsafe package file {deb}: {msg}")

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
        if not validate_backup_id(backup_id):
            return False
        src = (self.backup_dir / backup_id).resolve()
        if not src.is_dir() or not path_is_within(src, self.backup_dir.resolve()):
            return False
        man = src / "manifest.json"
        if not man.is_file():
            return False
        meta = json.loads(man.read_text(encoding="utf-8"))
        boot = Path("/boot").resolve()
        pre = sudo_prefix()
        for f in meta.get("files", []):
            if not isinstance(f, str) or not validate_boot_backup_filename(f):
                return False
            src_file = (src / f).resolve()
            if not path_is_within(src_file, src):
                return False
            if not src_file.is_file():
                return False
            dest = boot / f
            shutil.copy2(src_file, dest)
        subprocess.run(pre + ["update-grub"], capture_output=True)
        return True

    @staticmethod
    def _kernel_sort_key(release: str) -> tuple:
        match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", release)
        if match:
            parts = [int(x) for x in match.groups() if x is not None]
            while len(parts) < 3:
                parts.append(0)
            return tuple(parts)
        return (0, 0, 0)

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

    def find_linux_packages(self, kernel_version: str) -> List[str]:
        """Return installed linux-* package names tied to a kernel release string."""
        cp = run_cmd(["dpkg-query", "-W", "-f=${Package}\n", "linux-*"])
        if cp.returncode != 0:
            return []
        packages: List[str] = []
        suffix = f"-{kernel_version}"
        for line in cp.stdout.splitlines():
            name = line.strip()
            if not name.startswith("linux-"):
                continue
            if name.endswith(kernel_version) or suffix in name:
                packages.append(name)
        return sorted(set(packages))

    def remove_old_kernels(self, keep_count: int = 2, dry_run: bool = False) -> List[str]:
        """Remove old kernel packages, keeping the running kernel and the newest *keep_count*."""
        if keep_count < 0:
            raise ValueError("keep_count must be >= 0")
        current = os.uname().release
        installed = self.list_installed_kernels()
        installed.sort(key=lambda k: self._kernel_sort_key(k["version"]))
        candidates = [k for k in installed if k["version"] != current]
        if len(candidates) <= keep_count:
            return []
        to_remove = candidates[:-keep_count] if keep_count > 0 else candidates
        removed: List[str] = []
        for k in to_remove:
            ver = k["version"]
            pkgs = self.find_linux_packages(ver)
            if not pkgs:
                pkgs = [f"linux-image-{ver}", f"linux-headers-{ver}"]
            if dry_run:
                removed.append(f"[dry-run] would remove: {', '.join(pkgs)}")
                continue
            pre = sudo_prefix()
            cp = subprocess.run(
                pre + ["dpkg", "--purge", *pkgs],
                capture_output=True,
                text=True,
                timeout=600,
            )
            if cp.returncode == 0:
                removed.append(ver)
            else:
                removed.append(f"failed {ver}: {(cp.stderr or cp.stdout)[-500:]}")
        return removed

    def install_from_paths(
        self,
        package_list: List[Path],
        *,
        kernel_version_hint: Optional[str] = None,
        create_backup_first: bool = True,
    ) -> Tuple[bool, str, Tuple[bool, List[str]]]:
        """Install packages, optionally back up, and verify when a version hint is given."""
        if create_backup_first:
            self.create_backup()
        ok, log = self.install_packages(
            package_list,
            kernel_version_hint=kernel_version_hint,
        )
        verified = (False, ["no kernel version hint"])
        if kernel_version_hint:
            verified = self.verify_installation(kernel_version_hint)
        return ok, log, verified
