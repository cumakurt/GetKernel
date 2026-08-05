"""Install kernel DEB packages with optional backup metadata."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from modules.grub_manager import GrubManager
from utils.exceptions import InstallationError
from utils.helpers import is_root, run_cmd, sudo_prefix
from utils.validator import (
    check_file_safety,
    path_is_within,
    validate_backup_id,
    validate_boot_backup_filename,
    validate_kernel_release,
)


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

    @staticmethod
    def select_runtime_packages(package_list: List[Path]) -> List[Path]:
        """Select image/module/header packages needed to boot and build modules.

        ``bindeb-pkg`` also emits linux-libc-dev and sometimes a very large debug
        package. Installing those is unrelated to booting the new kernel and can
        replace distribution user-space headers, so they remain archived but are
        not installed automatically.
        """
        selected: List[Path] = []
        for deb in package_list:
            cp = run_cmd(["dpkg-deb", "-f", str(deb), "Package"])
            if cp.returncode != 0:
                continue
            package = cp.stdout.strip().splitlines()[0] if cp.stdout.strip() else ""
            if package.endswith(("-dbg", "-dbgsym")) or package == "linux-libc-dev":
                continue
            if package.startswith(("linux-image-", "linux-headers-", "linux-modules-")):
                selected.append(deb)
        return selected

    @staticmethod
    def _package_name(deb: Path) -> str:
        cp = run_cmd(["dpkg-deb", "-f", str(deb), "Package"], timeout=60)
        if cp.returncode != 0 or not cp.stdout.strip():
            return ""
        return cp.stdout.strip().splitlines()[0]

    @classmethod
    def _verify_package_states(cls, package_list: List[Path]) -> List[str]:
        """Confirm apt/dpkg left every requested package configured, not removed."""
        issues: List[str] = []
        for deb in package_list:
            package = cls._package_name(deb)
            if not package:
                issues.append(f"cannot read Package field from {deb.name}")
                continue
            cp = run_cmd(
                ["dpkg-query", "-W", "-f=${Status}", package],
                timeout=60,
            )
            if cp.returncode != 0 or cp.stdout.strip() != "install ok installed":
                detail = (cp.stderr or cp.stdout or "not installed").strip()
                issues.append(f"{package}: {detail}")
        return issues

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
        if kernel_version_hint and not validate_kernel_release(kernel_version_hint):
            raise InstallationError(f"Invalid kernel release hint: {kernel_version_hint!r}")

        pre = sudo_prefix()
        files = [str(p) for p in package_list]
        cp = run_cmd(
            pre + ["dpkg", "-i", *files],
            timeout=3600,
        )
        log = "$ dpkg -i\n" + (cp.stdout or "") + (cp.stderr or "")
        ok = cp.returncode == 0
        if not ok and fix_dependencies:
            env = os.environ.copy()
            env["DEBIAN_FRONTEND"] = "noninteractive"
            fix = run_cmd(
                pre + ["apt-get", "install", "-f", "-y", "-qq"],
                env=env,
                timeout=3600,
            )
            log += "\n$ apt-get install -f\n" + (fix.stdout or "") + (fix.stderr or "")
            ok = fix.returncode == 0

        if ok:
            state_issues = self._verify_package_states(package_list)
            if state_issues:
                ok = False
                log += "\nPackage state verification failed:\n" + "\n".join(state_issues)

        if ok and kernel_version_hint:
            initramfs = run_cmd(
                pre + ["update-initramfs", "-u", "-k", kernel_version_hint],
                timeout=1800,
            )
            log += "\n$ update-initramfs\n" + (initramfs.stdout or "") + (initramfs.stderr or "")
            ok = initramfs.returncode == 0
        if ok:
            grub = run_cmd(
                pre + ["update-grub"],
                timeout=600,
            )
            log += "\n$ update-grub\n" + (grub.stdout or "") + (grub.stderr or "")
            ok = grub.returncode == 0
        return ok, log

    def create_backup(self, kernel_version: Optional[str] = None) -> Optional[str]:
        if not is_root():
            return None
        kv = kernel_version or os.uname().release
        timestamp = datetime.now()
        for _ in range(60):
            bid = f"backup-{timestamp.strftime('%Y%m%d-%H%M%S')}"
            dest = self.backup_dir / bid
            try:
                dest.mkdir(parents=True, exist_ok=False)
                break
            except FileExistsError:
                timestamp += timedelta(seconds=1)
        else:
            raise InstallationError("Could not allocate a unique backup id")
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
        manifest = dest / "manifest.json"
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=dest,
            prefix=".manifest-",
            encoding="utf-8",
            delete=False,
        ) as tmp:
            json.dump(meta, tmp, indent=2)
            tmp.flush()
            os.fsync(tmp.fileno())
            temp_manifest = Path(tmp.name)
        temp_manifest.replace(manifest)
        return bid

    def rollback(self, backup_id: str) -> bool:
        if not validate_backup_id(backup_id):
            return False
        if not is_root():
            return False
        src = (self.backup_dir / backup_id).resolve()
        if not src.is_dir() or not path_is_within(src, self.backup_dir.resolve()):
            return False
        man = src / "manifest.json"
        if not man.is_file():
            return False
        try:
            meta = json.loads(man.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(meta, dict) or not isinstance(meta.get("files"), list):
            return False
        boot = Path("/boot").resolve()
        pre = sudo_prefix()
        restore_pairs: List[Tuple[Path, Path]] = []
        seen_files = set()
        for f in meta.get("files", []):
            if not isinstance(f, str) or not validate_boot_backup_filename(f):
                return False
            if f in seen_files:
                return False
            seen_files.add(f)
            src_file = (src / f).resolve()
            if not path_is_within(src_file, src):
                return False
            if not src_file.is_file():
                return False
            restore_pairs.append((src_file, boot / f))
        if not restore_pairs:
            return False

        staged: List[Tuple[Path, Path]] = []
        try:
            for src_file, dest in restore_pairs:
                with tempfile.NamedTemporaryFile(
                    dir=boot,
                    prefix=f".{dest.name}.",
                    delete=False,
                ) as tmp:
                    temp_dest = Path(tmp.name)
                shutil.copy2(src_file, temp_dest)
                staged.append((temp_dest, dest))
        except OSError:
            for temp_dest, _dest in staged:
                temp_dest.unlink(missing_ok=True)
            return False

        for temp_dest, dest in staged:
            try:
                # Every source was validated and copied successfully before the
                # first live /boot file is replaced.
                temp_dest.replace(dest)
            except OSError:
                for remaining, _target in staged:
                    remaining.unlink(missing_ok=True)
                return False
        return run_cmd(pre + ["update-grub"], timeout=600).returncode == 0

    @staticmethod
    def _kernel_sort_key(release: str) -> tuple:
        match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", release)
        if match:
            parts = [int(x) for x in match.groups() if x is not None]
            while len(parts) < 3:
                parts.append(0)
            rc_match = re.search(r"-rc(\d+)", release)
            # A final release sorts after its release candidates.
            prerelease_rank = 0 if rc_match else 1
            rc_number = int(rc_match.group(1)) if rc_match else 0
            return (*parts, prerelease_rank, rc_number, release)
        return (0, 0, 0, 0, 0, release)

    def list_backups(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not self.backup_dir.is_dir():
            return out
        for d in sorted(self.backup_dir.iterdir()):
            if not d.is_dir():
                continue
            man = d / "manifest.json"
            if man.is_file():
                try:
                    meta = json.loads(man.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(meta, dict):
                    continue
                try:
                    size = sum(
                        path.stat().st_size
                        for path in d.iterdir()
                        if path.is_file()
                    )
                except OSError:
                    size = 0
                out.append(
                    {
                        "id": d.name,
                        "kernel_version": meta.get("kernel", ""),
                        "date": d.name.replace("backup-", ""),
                        "size_bytes": size,
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
        if not validate_kernel_release(kernel_version):
            return False, [f"invalid kernel release: {kernel_version!r}"]
        if not (Path("/boot") / f"vmlinuz-{kernel_version}").is_file():
            issues.append(f"missing /boot/vmlinuz-{kernel_version}")
        moddir = Path("/lib/modules") / kernel_version
        if not moddir.is_dir():
            issues.append(f"missing {moddir}")
        else:
            build_dir = moddir / "build"
            if not build_dir.exists():
                issues.append(
                    f"missing {build_dir} (install matching linux-headers; "
                    "VMware/DKMS modules cannot be built without it)"
                )
            elif not (build_dir / "Makefile").is_file():
                issues.append(f"incomplete kernel headers under {build_dir}")
        return len(issues) == 0, issues

    def set_default_kernel(self, kernel_version: str) -> bool:
        return GrubManager().set_default_entry(kernel_version=kernel_version)

    def list_installed_kernels(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        boot = Path("/boot")
        if not boot.is_dir():
            return out
        current = os.uname().release
        for p in sorted(boot.glob("vmlinuz-*")):
            ver = p.name.replace("vmlinuz-", "")
            try:
                stat = p.stat()
            except OSError:
                continue
            out.append(
                {
                    "version": ver,
                    "image": str(p),
                    "size_bytes": stat.st_size,
                    "installed_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "is_running": ver == current,
                    "is_default": None,
                }
            )
        out.sort(
            key=lambda item: (
                bool(item["is_running"]),
                self._kernel_sort_key(str(item["version"])),
            ),
            reverse=True,
        )
        return out

    def find_linux_packages(self, kernel_version: str) -> List[str]:
        """Return installed linux-* package names tied to a kernel release string."""
        if not validate_kernel_release(kernel_version):
            return []
        cp = run_cmd(["dpkg-query", "-W", "-f=${Package}\n", "linux-*"])
        if cp.returncode != 0:
            return []
        packages: List[str] = []
        pattern = re.compile(
            rf"^linux-(?:image(?:-unsigned)?|headers|modules(?:-extra)?)-"
            rf"{re.escape(kernel_version)}(?:-dbg|-dbgsym)?(?::[A-Za-z0-9_-]+)?$"
        )
        for line in cp.stdout.splitlines():
            name = line.strip()
            if pattern.fullmatch(name):
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
            cp = run_cmd(
                pre + ["dpkg", "--purge", *pkgs],
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
        if kernel_version_hint and ok:
            verified = self.verify_installation(kernel_version_hint)
        elif kernel_version_hint:
            verified = (False, ["installation command failed"])
        return ok, log, verified
