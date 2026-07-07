#!/usr/bin/env python3
"""
GetKernel — main CLI entry (orchestrator).

Developer: Cuma KURT <cumakurt@gmail.com>
Project: https://github.com/cumakurt/GetKernel
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import click

from modules.compiler import Compiler
from modules.config_manager import ConfigManager
from modules.dependency_manager import DependencyManager
from modules.installer import Installer
from modules.kernel_fetcher import KernelFetcher
from modules.package_builder import PackageBuilder, find_matching_stored_packages
from modules.grub_manager import GrubManager
from modules.package_depot import (
    list_archived_builds,
    list_latest_packages,
    read_build_history,
    resolve_package_paths,
)
from modules.system_advisor import (
    collect_build_warnings,
    collect_install_warnings,
    status_secure_boot,
)
from modules.system_checker import SystemChecker
from modules.uninstaller import detect_remnants, uninstall_getkernel
from utils.constants import (
    APP_VERSION,
    DEVELOPER_EMAIL,
    DEVELOPER_GITHUB_REPO_URL,
    DEVELOPER_LINKEDIN_URL,
    DEVELOPER_NAME,
)
from utils.exceptions import ConfigError, DependencyError, GetKernelError
from utils.helpers import (
    assume_yes_from_env,
    ensure_elevated,
    generate_build_id,
    load_yaml_config,
    merge_dict,
    project_root,
    resolve_path,
)
from utils.logger import log_build_event, log_exception, setup_logging
from utils.ui import (
    banner,
    build_progress_display,
    confirm,
    print_build_step_summary,
    print_build_success_summary,
    print_error_block,
    print_interactive_snapshot,
    print_step,
    print_table,
    prompt_kernel_selection,
)
from utils.validator import validate_backup_id, validate_build_id, validate_kernel_version

_VERSION_MESSAGE = (
    "%(prog)s %(version)s\n"
    f"{DEVELOPER_NAME} <{DEVELOPER_EMAIL}>\n"
    f"{DEVELOPER_GITHUB_REPO_URL}"
)


def _emit_json(data: Any, *, exit_code: int = 0) -> None:
    click.echo(json.dumps(data, indent=2, default=str))
    sys.exit(exit_code)


def _make_fetcher(cfg: Dict[str, Any], cache_dir: Path) -> KernelFetcher:
    return KernelFetcher.from_config(str(cache_dir), cfg.get("kernel") or {})


def _resolve_profile_path(cfg: Dict[str, Any], profile: str) -> Path:
    build_cfg = cfg.get("build") or {}
    profiles = build_cfg.get("profiles") or {}
    rel = profiles.get(profile)
    if not rel:
        raise click.UsageError(
            f"Unknown profile {profile!r}. Available: {', '.join(sorted(profiles)) or '(none)'}"
        )
    path = resolve_path(project_root(), str(rel))
    if not path.is_file():
        raise click.UsageError(f"Profile file not found: {path}")
    return path


def _latest_build_log(logs_dir: Path) -> Optional[Path]:
    if not logs_dir.is_dir():
        return None
    logs = sorted(logs_dir.glob("build-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


def _load_config() -> Dict[str, Any]:
    root = project_root()
    base = load_yaml_config(root / "config" / "default_config.yaml")
    user = load_yaml_config(root / "config" / "user_config.yaml")
    return merge_dict(base, user)


def _paths(cfg: Dict[str, Any]) -> Dict[str, Path]:
    root = project_root()
    p = cfg.get("paths") or {}
    return {
        "cache": resolve_path(root, str(p.get("cache_dir", "data/cache"))),
        "logs": resolve_path(root, str(p.get("log_dir", "data/logs"))),
        "builds": resolve_path(root, str(p.get("build_root", "data/builds"))),
        "packages": resolve_path(root, str(p.get("packages_dir", "data/packages"))),
    }


def _collect_config_fragments(
    cfg: Dict[str, Any],
    root: Path,
    cli_fragments: Optional[List[str]],
) -> List[Path]:
    """Paths from config build.config_fragments plus CLI --fragment (order preserved, deduped)."""
    build_cfg = cfg.get("build") or {}
    frag_paths: List[Path] = []
    for f in build_cfg.get("config_fragments") or []:
        if isinstance(f, str) and f.strip():
            frag_paths.append(resolve_path(root, f.strip()))
    for c in cli_fragments or []:
        if c:
            frag_paths.append(Path(c).resolve())
    seen: Set[Path] = set()
    uniq: List[Path] = []
    for p in frag_paths:
        r = p.resolve()
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    return uniq


def _prompt_rebuild_or_quit(
    version: str,
    pkg_out: Path,
    debs: List[Path],
) -> str:
    """Return ``rebuild`` or ``quit`` when stored packages exist (depot install is disabled)."""
    click.echo("")
    click.echo(
        click.style(
            "Stored packages for this kernel version already exist in the package depot.",
            fg="yellow",
            bold=True,
        )
    )
    click.echo(
        "Only a fresh build can be installed; stored packages are not offered for installation."
    )
    click.echo(f"  Version: {version}")
    click.echo(f"  Directory: {pkg_out / 'latest'}")
    for p in debs:
        click.echo(f"  • {p.name}")
    if not sys.stdin.isatty():
        click.echo(
            click.style(
                "Non-interactive: rebuilding. Use --force-rebuild to skip this notice.",
                fg="dim",
            ),
            err=True,
        )
        return "rebuild"
    c = click.prompt(
        "Choice [r]ebuild / [q]uit",
        default="r",
        type=click.Choice(["r", "q"], case_sensitive=False),
    )
    return "quit" if c == "q" else "rebuild"


def _install_kernel_packages_phase(
    moved: List[Path],
    version: str,
    localver: str,
    skip_install: bool,
    assume_yes_install: bool,
    log: logging.Logger,
    *,
    build_log: Optional[Path] = None,
) -> None:
    """After a fresh build: success message and optional install prompt (latest build only)."""
    if not moved:
        return
    if skip_install:
        print_build_success_summary(len(moved), moved[0].parent, build_log)
        click.echo("Packages:")
        for p in moved:
            click.echo(f"  {p}")
        click.echo("Skipping installation (--skip-install).")
        return
    print_build_success_summary(len(moved), moved[0].parent, build_log)
    inst = Installer()
    install_yes = assume_yes_install or assume_yes_from_env()
    if not inst.request_installation_approval(
        moved,
        assume_yes=install_yes,
        default_confirm=True,
    ):
        click.echo("Installation skipped.")
        return
    hint = f"{version}{localver}"
    try:
        ok, ilog, (verified, issues) = inst.install_from_paths(
            moved,
            kernel_version_hint=hint,
            create_backup_first=True,
        )
        click.echo(ilog[-2000:] if len(ilog) > 2000 else ilog)
        if not ok:
            click.echo("Installation reported errors; check logs.", err=True)
            sys.exit(1)
        if verified:
            click.echo(click.style(f"Verified installation for {hint}.", fg="green"))
        else:
            click.echo(
                click.style(
                    "Installation finished but verification reported issues: "
                    + "; ".join(issues),
                    fg="yellow",
                )
            )
    except GetKernelError as exc:
        log_exception(log, exc, {})
        raise click.Abort() from exc
    click.echo("Done. Reboot to boot the new kernel when ready.")


def _ensure_build_dependencies(cfg: Dict[str, Any], log: logging.Logger) -> None:
    """If enabled in config, run apt-get update and install all missing build packages."""
    dep = cfg.get("dependencies") or {}
    if not dep.get("auto_install", True):
        click.echo(
            click.style(
                "Skipping automatic apt install (dependencies.auto_install is false).",
                fg="yellow",
            )
        )
        return
    dm = DependencyManager()
    include_opt = bool(dep.get("install_optional", False))
    missing = dm.get_missing_packages(include_optional=include_opt)
    if not missing:
        click.echo(click.style("Build dependencies satisfied.", fg="green"))
        return
    click.echo("Missing packages: " + ", ".join(missing))
    click.echo("Installing via apt (non-interactive) …")
    try:
        if dep.get("apt_update", True):
            if not dm.update_package_cache():
                click.echo("apt-get update failed; continuing.", err=True)
        ok, failed = dm.install_all_dependencies(include_optional=include_opt)
    except DependencyError as exc:
        click.echo(
            click.style(
                "Cannot install packages without root. Run: sudo getkernel …",
                fg="red",
            ),
            err=True,
        )
        log_exception(log, exc, {})
        raise click.Abort() from exc
    if not ok:
        click.echo("Failed to install: " + ", ".join(failed), err=True)
        log_exception(log, RuntimeError("apt install failed"), {"failed": failed})
        raise click.Abort()
    click.echo(click.style("Dependencies installed.", fg="green"))


@click.group(invoke_without_command=True)
@click.option(
    "--yes",
    "-y",
    "assume_yes",
    is_flag=True,
    help="Assume yes for package installation prompts (non-interactive).",
)
@click.pass_context
@click.version_option(APP_VERSION, prog_name="getkernel", message=_VERSION_MESSAGE)
def cli(ctx: click.Context, assume_yes: bool) -> None:
    """Build and install Linux kernel packages on Debian-based systems."""
    ctx.ensure_object(dict)
    ctx.obj["assume_yes"] = assume_yes
    if ctx.invoked_subcommand is None:
        ctx.invoke(interactive)


@cli.command("check")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON")
def cmd_check(as_json: bool) -> None:
    """Validate OS, disk, RAM, and toolchain."""
    cfg = _load_config()
    paths = _paths(cfg)
    setup_logging(paths["logs"], **cfg.get("logging", {}))
    sc = SystemChecker()
    vr = sc.validate_environment()
    payload = {
        "valid": vr.is_valid,
        "debian_based": sc.is_debian_based(),
        "root_or_sudo": sc.check_root_privileges(),
        "kernel": sc.get_current_kernel_version(),
        "errors": vr.errors,
        "warnings": vr.warnings,
        "secure_boot": status_secure_boot(),
    }
    if as_json:
        _emit_json(payload, exit_code=0 if vr.is_valid else 1)
    print_table(
        "System check",
        [
            {"field": "Debian-based", "value": str(sc.is_debian_based())},
            {"field": "Root/sudo", "value": str(sc.check_root_privileges())},
            {"field": "Kernel", "value": sc.get_current_kernel_version()},
        ],
        ["field", "value"],
    )
    for e in vr.errors:
        click.echo(click.style(f"Error: {e}", fg="red"))
    for w in vr.warnings:
        click.echo(click.style(f"Warning: {w}", fg="yellow"))
    sys.exit(0 if vr.is_valid else 1)


@cli.command("about")
def cmd_about() -> None:
    """Show developer name, contact, and project links."""
    click.echo(f"GetKernel {APP_VERSION}")
    click.echo(f"Name:     {DEVELOPER_NAME}")
    click.echo(f"Email:    {DEVELOPER_EMAIL}")
    click.echo(f"LinkedIn: {DEVELOPER_LINKEDIN_URL}")
    click.echo(f"GitHub:   {DEVELOPER_GITHUB_REPO_URL}")


@cli.command("list")
@click.option("--no-rc", is_flag=True, help="Hide release candidates")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON")
def cmd_list(no_rc: bool, as_json: bool) -> None:
    """List kernel versions from kernel.org."""
    cfg = _load_config()
    paths = _paths(cfg)
    setup_logging(paths["logs"], **cfg.get("logging", {}))
    fetcher = _make_fetcher(cfg, paths["cache"])
    data = fetcher.fetch_kernel_versions(include_rc=not no_rc)
    rows = []
    for v in data.get("versions", [])[:40]:
        rows.append(
            {
                "version": v.get("version", ""),
                "type": v.get("type", ""),
                "released": v.get("released", ""),
            }
        )
    if as_json:
        _emit_json({"stable": data.get("stable"), "mainline": data.get("mainline"), "versions": rows})
    print_table("Kernel versions (kernel.org)", rows, ["version", "type", "released"])


@cli.command("deps")
@click.option("--install", is_flag=True, help="Install missing packages via apt")
def cmd_deps(install: bool) -> None:
    """Show or install build dependencies."""
    cfg = _load_config()
    paths = _paths(cfg)
    log = setup_logging(paths["logs"], **cfg.get("logging", {}))
    dm = DependencyManager(auto_install=install)
    missing = dm.get_missing_packages()
    if not missing:
        click.echo("All required packages are installed.")
        return
    click.echo("Missing: " + ", ".join(missing))
    if install:
        if not dm.update_package_cache():
            click.echo("apt-get update failed (continuing).", err=True)
        ok, failed = dm.install_all_dependencies()
        if not ok:
            log_exception(log, RuntimeError("apt install failed"), {"failed": failed})
            raise click.Abort()
        click.echo("Dependencies installed.")
    else:
        click.echo("Run with --install to apt-get install these packages.")


@cli.command("cleanup")
@click.option("--old-kernels", is_flag=True, help="Remove old kernel packages (keep running + 2 newest)")
@click.option("--build-artifacts", is_flag=True, help="Remove intermediate build files from data/builds")
@click.option("--keep", type=int, default=2, help="Number of old kernels to keep (default: 2)")
@click.option("--dry-run", is_flag=True, help="Show what would be removed without deleting")
def cmd_cleanup(old_kernels: bool, build_artifacts: bool, keep: int, dry_run: bool) -> None:
    """Remove old kernels and/or build artifacts."""
    if keep < 0:
        raise click.BadParameter("--keep must be >= 0")
    if not old_kernels and not build_artifacts:
        click.echo("Specify --old-kernels and/or --build-artifacts. See: getkernel cleanup --help")
        return
    cfg = _load_config()
    paths = _paths(cfg)
    if old_kernels:
        inst = Installer()
        try:
            removed = inst.remove_old_kernels(keep_count=keep, dry_run=dry_run)
        except ValueError as exc:
            raise click.BadParameter(str(exc)) from exc
        if removed:
            for r in removed:
                click.echo(f"  {r}")
        else:
            click.echo("No old kernels to remove.")
    if build_artifacts:
        builds = paths["builds"]
        if not builds.is_dir():
            click.echo("No build directory found.")
            return
        for src_dir in sorted(builds.iterdir()):
            if not src_dir.is_dir() or src_dir.name.startswith("."):
                continue
            pb = PackageBuilder(str(src_dir))
            count = pb.cleanup_build_artifacts(keep_packages=True, dry_run=dry_run)
            if dry_run:
                click.echo(
                    f"  {src_dir.name}: would remove {count} intermediate file(s) (dry-run)"
                )
            else:
                click.echo(f"  {src_dir.name}: removed {count} intermediate file(s)")


@cli.command("status")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON")
def cmd_status(as_json: bool) -> None:
    """Show running kernel, installed kernels, GRUB, depot, and last build log."""
    cfg = _load_config()
    paths = _paths(cfg)
    setup_logging(paths["logs"], **cfg.get("logging", {}))
    inst = Installer()
    gm = GrubManager()
    payload: Dict[str, Any] = {
        "running_kernel": os.uname().release,
        "installed_kernels": inst.list_installed_kernels(),
        "grub_default": gm.get_default_entry(),
        "grub_entries": gm.get_menu_entries()[:10],
        "packages_latest": list_latest_packages(paths["packages"]),
        "packages_archives": list_archived_builds(paths["packages"]),
        "build_history": read_build_history(paths["packages"], limit=5),
        "last_build_log": str(_latest_build_log(paths["logs"]) or ""),
        "secure_boot": status_secure_boot(),
    }
    if as_json:
        _emit_json(payload)
    click.echo(f"Running kernel: {payload['running_kernel']}")
    click.echo(f"GRUB default: {payload['grub_default'] or '(unknown)'}")
    click.echo(f"Secure Boot: {payload['secure_boot']['note']}")
    print_table("Installed kernels", payload["installed_kernels"][:10], ["version", "is_running"])
    latest = payload["packages_latest"]
    click.echo(f"Package depot (latest): {len(latest)} .deb file(s)")
    if payload["last_build_log"]:
        click.echo(f"Last build log: {payload['last_build_log']}")


@cli.command("install")
@click.pass_context
@click.option("--build-id", default=None, help="Install packages from archive/build-<id>/ instead of latest/")
@click.option("--kernel-version", default=None, help="Expected kernel release for post-install verification")
def cmd_install(ctx: click.Context, build_id: Optional[str], kernel_version: Optional[str]) -> None:
    """Install .deb packages from the package depot (latest or archived build)."""
    if build_id is not None and not validate_build_id(build_id):
        click.echo("Invalid --build-id (expected 12 lowercase hex characters).", err=True)
        sys.exit(1)
    cfg = _load_config()
    paths = _paths(cfg)
    log = setup_logging(paths["logs"], **cfg.get("logging", {}))
    packages = resolve_package_paths(paths["packages"], build_id=build_id)
    if not packages:
        click.echo("No packages found in the depot.", err=True)
        sys.exit(1)
    inst = Installer()
    assume_yes = bool(ctx.obj.get("assume_yes")) or assume_yes_from_env()
    for warning in collect_install_warnings(kernel_version or ""):
        click.echo(click.style(f"Warning: {warning}", fg="yellow"))
    if not inst.request_installation_approval(packages, assume_yes=assume_yes):
        click.echo("Installation cancelled.")
        return
    hint = kernel_version
    if not hint:
        info_path = paths["packages"] / "latest" / "build-info.json"
        if info_path.is_file():
            try:
                meta = json.loads(info_path.read_text(encoding="utf-8"))
                rv = meta.get("requested_version", "")
                lv = meta.get("localversion", "")
                if rv:
                    hint = f"{rv}{lv}"
            except (OSError, ValueError, json.JSONDecodeError):
                hint = None
    try:
        ok, ilog, (verified, issues) = inst.install_from_paths(
            packages,
            kernel_version_hint=hint,
            create_backup_first=True,
        )
        click.echo(ilog[-2000:] if len(ilog) > 2000 else ilog)
        if not ok:
            sys.exit(1)
        if hint and verified:
            click.echo(click.style(f"Verified installation for {hint}.", fg="green"))
        elif hint:
            click.echo(
                click.style("Verification issues: " + "; ".join(issues), fg="yellow")
            )
    except GetKernelError as exc:
        log_exception(log, exc, {})
        raise click.Abort() from exc


@cli.group("packages")
def cmd_packages_group() -> None:
    """Inspect built kernel packages in the depot."""


@cmd_packages_group.command("list")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON")
def cmd_packages_list(as_json: bool) -> None:
    """List packages under latest/ and archived builds."""
    cfg = _load_config()
    paths = _paths(cfg)
    payload = {
        "latest": list_latest_packages(paths["packages"]),
        "archives": list_archived_builds(paths["packages"]),
        "history": read_build_history(paths["packages"]),
    }
    if as_json:
        _emit_json(payload)
    print_table("Latest packages", payload["latest"], ["name", "size_bytes", "built_at"])
    print_table("Archived builds", payload["archives"], ["build_id", "deb_count", "built_at"])


@cli.command("backups")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON")
def cmd_backups(as_json: bool) -> None:
    """List boot file backups created before kernel installation."""
    inst = Installer()
    rows = inst.list_backups()
    if as_json:
        _emit_json({"backups": rows})
    if not rows:
        click.echo("No backups found.")
        return
    print_table("GetKernel backups", rows, ["id", "kernel_version", "date"])


@cli.command("rollback")
@click.argument("backup_id")
def cmd_rollback(backup_id: str) -> None:
    """Restore /boot files from a backup created by GetKernel."""
    if not validate_backup_id(backup_id):
        click.echo(
            "Invalid backup id (expected format: backup-YYYYMMDD-HHMMSS).",
            err=True,
        )
        sys.exit(1)
    inst = Installer()
    if not inst.rollback(backup_id):
        click.echo(f"Rollback failed for backup {backup_id!r}.", err=True)
        sys.exit(1)
    click.echo(click.style(f"Restored backup {backup_id}. Run reboot when ready.", fg="green"))


@cli.command("uninstall")
@click.option("--yes", "-y", "assume_yes", is_flag=True, help="Skip confirmation")
def cmd_uninstall(assume_yes: bool) -> None:
    """Remove GetKernel from /usr/local/getkernel and related symlinks."""
    paths, rc_files = detect_remnants()
    if not paths and not rc_files:
        click.echo("No GetKernel installation remnants detected.")
        return
    click.echo("Will remove:")
    from utils.constants import GETKERNEL_INSTALL_DIR

    if any(p.resolve() == GETKERNEL_INSTALL_DIR.resolve() for p in paths):
        click.echo(
            click.style(
                "  Warning: this deletes all runtime data under data/cache, "
                "data/builds, data/logs, and data/packages.",
                fg="yellow",
            )
        )
    for p in paths:
        click.echo(f"  {p}")
    for f in rc_files:
        click.echo(f"  PATH snippet in {f}")
    if not assume_yes and not confirm("Proceed with uninstall?", default=False):
        click.echo("Cancelled.")
        return
    try:
        removed = uninstall_getkernel()
    except PermissionError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)
    click.echo(click.style("Removed:", fg="green"))
    for item in removed:
        click.echo(f"  {item}")


def run_build_flow(
    version: str,
    source_dir: Optional[str],
    skip_install: bool,
    *,
    dry_run: bool = False,
    config_path: Optional[str] = None,
    assume_yes_install: bool = False,
    packages_output_dir: Optional[str] = None,
    quiet_build: bool = False,
    verbose_build: bool = False,
    config_fragments: Optional[List[str]] = None,
    use_llvm: bool = False,
    localmodconfig: bool = False,
    force_rebuild: bool = False,
    menuconfig: bool = False,
    resume_build: bool = False,
    profile: Optional[str] = None,
) -> None:
    """Download, configure, compile, package, and optionally install a kernel."""
    cfg = _load_config()
    paths = _paths(cfg)
    log = setup_logging(paths["logs"], **cfg.get("logging", {}))

    # Validate kernel version to prevent path traversal or malformed URLs
    if not source_dir and not validate_kernel_version(version):
        click.echo(
            click.style(
                f"Invalid kernel version format: {version!r}. "
                "Expected something like 6.12.8 or 6.13-rc1.",
                fg="red",
            ),
            err=True,
        )
        sys.exit(1)

    kv = cfg.get("kernel", {})
    localver = str(kv.get("localversion", "-getkernel"))
    build_cfg = cfg.get("build") or {}
    pkg_target = str(build_cfg.get("target", "bindeb-pkg"))
    root = project_root()
    pkg_out = (
        resolve_path(root, packages_output_dir)
        if packages_output_dir
        else paths["packages"]
    )

    sc = SystemChecker()
    vr = sc.validate_environment()
    if not vr.is_valid:
        for e in vr.errors:
            print_error_block("Environment check failed", e, vr.recommendations)
        sys.exit(1)

    for warning in collect_build_warnings(version):
        click.echo(click.style(f"Warning: {warning}", fg="yellow"))
        if sys.stdin.isatty() and not assume_yes_install and not assume_yes_from_env():
            if not confirm("Continue despite warnings?", default=True):
                click.echo("Cancelled.")
                return

    has_frags = bool(config_fragments) or bool(build_cfg.get("config_fragments")) or bool(profile)
    reuse_allowed = (
        not dry_run
        and not force_rebuild
        and not source_dir
        and not config_path
        and not has_frags
        and not (bool(build_cfg.get("use_llvm")) or use_llvm)
        and not (bool(build_cfg.get("localmodconfig")) or localmodconfig)
        and not menuconfig
        and not resume_build
        and not profile
    )
    if reuse_allowed:
        existing = find_matching_stored_packages(pkg_out, version, localver)
        if existing:
            action = _prompt_rebuild_or_quit(version, pkg_out, existing)
            if action == "quit":
                return

    _ensure_build_dependencies(cfg, log)

    if source_dir:
        src = Path(source_dir).resolve()
        if not (src / "Makefile").is_file():
            click.echo("Invalid kernel source (no Makefile).", err=True)
            sys.exit(1)
    else:
        fetcher = _make_fetcher(cfg, paths["cache"])
        reuse = bool(kv.get("reuse_downloads", True))
        click.echo(f"Preparing kernel source linux-{version} …")
        try:
            extracted, prep_status = fetcher.download_kernel_source(
                version,
                target_dir=str(paths["builds"]),
                reuse_existing=reuse,
                verify_signature=bool(kv.get("verify_signature", False)),
            )
        except GetKernelError as exc:
            log_exception(log, exc, {})
            raise click.Abort() from exc
        if prep_status == "reuse_tree":
            click.echo(click.style("Reusing existing source tree (skip download).", fg="green"))
        elif prep_status == "reuse_tarball":
            click.echo(click.style("Reusing cached tarball (skip download).", fg="green"))
        elif prep_status == "resume":
            click.echo(click.style("Resuming interrupted download …", fg="green"))
        src = Path(extracted)

    use_llvm_build = bool(build_cfg.get("use_llvm", False)) or use_llvm
    use_lmc_build = bool(build_cfg.get("localmodconfig", False)) or localmodconfig

    cm = ConfigManager(str(src))
    try:
        if config_path:
            cf = Path(config_path).resolve()
            if not cf.is_file():
                raise ConfigError(f"Kernel config file not found: {cf}")
            click.echo(f"Using kernel config from {cf} …")
            base = cf.read_text(encoding="utf-8", errors="replace")
            cm.create_new_config(base)
        else:
            click.echo("Applying running kernel configuration …")
            base = cm.get_current_config()
            cm.create_new_config(base)
        frag_paths = _collect_config_fragments(cfg, root, config_fragments)
        if profile:
            frag_paths.append(_resolve_profile_path(cfg, profile))
        if frag_paths:
            click.echo(f"Merging {len(frag_paths)} config fragment(s) …")
            cm.merge_config_fragments(frag_paths)
        if menuconfig:
            click.echo("Launching make menuconfig …")
            cm.run_menuconfig()
        if use_lmc_build:
            click.echo("Running make localmodconfig …")
            cm.run_localmodconfig()
    except GetKernelError as exc:
        log_exception(log, exc, {})
        raise click.Abort() from exc

    comp = Compiler(str(src))
    if not use_llvm_build:
        comp.enable_ccache()
    last_build_log: Optional[Path] = None
    try:
        if resume_build and comp.has_partial_build():
            click.echo(click.style("Resuming previous partial build …", fg="green"))
            comp.prepare_source(resume=True)
        else:
            comp.prepare_source()
        if dry_run:
            click.echo(
                click.style(
                    "Dry run: source prepared and .config applied; skipping compile.",
                    fg="green",
                )
            )
            click.echo(f"Kernel tree: {src}")
            click.echo(f"Packages output (when built): {pkg_out}")
            return
        make_t, _ = comp.resolve_make_package_target(pkg_target)
        build_id = generate_build_id()
        build_log = paths["logs"] / f"build-{build_id}.log"
        last_build_log = build_log
        log_build_event(
            log,
            "build_start",
            build_id,
            {
                "version": version,
                "target": make_t,
                "log": str(build_log),
                "llvm": use_llvm_build,
            },
        )
        if use_llvm_build:
            click.echo("Building with LLVM=1 (clang); ensure clang/llvm are installed.", err=True)

        compile_kwargs = {
            "target": pkg_target,
            "jobs": build_cfg.get("jobs"),
            "local_version": localver,
            "log_path": build_log,
            "build_id": build_id,
            "use_llvm": use_llvm_build,
        }
        if verbose_build:
            click.echo(f"Starting build (make {make_t}); full log → {build_log}")
            comp.compile_kernel(**compile_kwargs, verbose=True)
        elif quiet_build:
            click.echo(f"Building (make {make_t}); log → {build_log}")
            comp.compile_kernel(**compile_kwargs, verbose=False)
        else:
            with build_progress_display(build_log, make_t) as on_progress:
                comp.compile_kernel(
                    **compile_kwargs,
                    verbose=False,
                    progress_callback=on_progress,
                )
        log_build_event(
            log,
            "build_done",
            build_id,
            {
                "seconds": comp.estimated_duration,
                "log": str(build_log),
            },
        )
    except GetKernelError as exc:
        log_exception(
            log,
            exc,
            {
                "build_id": getattr(comp, "build_id", None),
                "log": str(getattr(comp, "last_build_log_path", "") or ""),
            },
        )
        raise click.Abort() from exc

    pb = PackageBuilder(str(src), output_dir=str(pkg_out))
    debs = pb.find_built_packages()
    if not debs:
        click.echo("No .deb files found next to the build directory.", err=True)
        sys.exit(1)
    ok, errs = pb.verify_packages(debs)
    if not ok:
        click.echo("Package verification failed: " + "; ".join(errs), err=True)
        sys.exit(1)
    moved = pb.move_packages(
        debs,
        requested_version=version,
        localversion=localver,
        build_id=build_id if not dry_run else None,
    )
    _install_kernel_packages_phase(
        moved,
        version,
        localver,
        skip_install,
        assume_yes_install,
        log,
        build_log=last_build_log,
    )


@cli.command("build")
@click.pass_context
@click.option("--version", "version", required=True, help="Kernel version, e.g. 6.12.8")
@click.option(
    "--source-dir",
    type=click.Path(),
    default=None,
    help="Existing kernel source tree (skip download)",
)
@click.option("--skip-install", is_flag=True, help="Do not prompt for dpkg install")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Prepare source and kernel config only; do not compile or produce .deb files.",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(),
    default=None,
    help="Path to a kernel .config file (instead of the running kernel's config).",
)
@click.option(
    "--output-dir",
    "packages_output_dir",
    type=click.Path(),
    default=None,
    help="Where to collect built .deb packages (default: paths.packages_dir from config).",
)
@click.option(
    "--quiet",
    "-q",
    "quiet_build",
    is_flag=True,
    help="Minimal terminal output during build (no live progress panel; log file only).",
)
@click.option(
    "--verbose",
    "-v",
    "verbose_build",
    is_flag=True,
    help="Stream full make output to the terminal (disables live progress panel).",
)
@click.option(
    "--fragment",
    "config_fragments",
    multiple=True,
    type=click.Path(),
    default=None,
    help="Kconfig fragment file(s) merged after base (scripts/kconfig/merge_config.sh). Repeatable.",
)
@click.option(
    "--llvm",
    "use_llvm",
    is_flag=True,
    help="Build with LLVM=1 (clang); install clang/llvm first.",
)
@click.option(
    "--localmodconfig",
    "use_localmodconfig",
    is_flag=True,
    help="After base config, run make localmodconfig (trim to loaded modules).",
)
@click.option(
    "--force-rebuild",
    is_flag=True,
    help="Ignore stored .deb packages for this version; always run a full build.",
)
@click.option(
    "--menuconfig",
    is_flag=True,
    help="Run interactive make menuconfig after merging config fragments.",
)
@click.option(
    "--resume-build",
    is_flag=True,
    help="Skip make clean/prepare when a partial build and .config already exist.",
)
@click.option(
    "--profile",
    type=click.Choice(["minimal", "server", "desktop"], case_sensitive=False),
    default=None,
    help="Apply a named Kconfig profile from config/profiles/.",
)
def cmd_build(
    ctx: click.Context,
    version: str,
    source_dir: Optional[str],
    skip_install: bool,
    dry_run: bool,
    config_path: Optional[str],
    packages_output_dir: Optional[str],
    quiet_build: bool,
    verbose_build: bool,
    config_fragments: tuple,
    use_llvm: bool,
    use_localmodconfig: bool,
    force_rebuild: bool,
    menuconfig: bool,
    resume_build: bool,
    profile: Optional[str],
) -> None:
    """Download kernel, configure from running system, compile deb-pkg."""
    if quiet_build and verbose_build:
        raise click.UsageError("--quiet and --verbose cannot be used together.")
    install_yes = bool(ctx.obj.get("assume_yes")) or assume_yes_from_env()
    frags = list(config_fragments) if config_fragments else None
    run_build_flow(
        version,
        source_dir,
        skip_install,
        dry_run=dry_run,
        config_path=config_path,
        assume_yes_install=install_yes,
        packages_output_dir=packages_output_dir,
        quiet_build=quiet_build,
        verbose_build=verbose_build,
        config_fragments=frags,
        use_llvm=use_llvm,
        localmodconfig=use_localmodconfig,
        force_rebuild=force_rebuild,
        menuconfig=menuconfig,
        resume_build=resume_build,
        profile=profile,
    )


@cli.command("prepare")
@click.pass_context
@click.option("--version", "version", required=True, help="Kernel version, e.g. 6.12.8")
@click.option(
    "--source-dir",
    type=click.Path(),
    default=None,
    help="Existing kernel source tree (skip download)",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(),
    default=None,
    help="Path to a kernel .config file (instead of the running kernel's config).",
)
@click.option(
    "--output-dir",
    "packages_output_dir",
    type=click.Path(),
    default=None,
    help="Where packages would be collected after a full build (informational for dry run).",
)
@click.option(
    "--fragment",
    "config_fragments",
    multiple=True,
    type=click.Path(),
    default=None,
    help="Kconfig fragment file(s) merged after base. Repeatable.",
)
@click.option(
    "--localmodconfig",
    "use_localmodconfig",
    is_flag=True,
    help="Run make localmodconfig after merging fragments.",
)
def cmd_prepare(
    _ctx: click.Context,
    version: str,
    source_dir: Optional[str],
    config_path: Optional[str],
    packages_output_dir: Optional[str],
    config_fragments: tuple,
    use_localmodconfig: bool,
) -> None:
    """Download and configure kernel source only (same as: build --dry-run --skip-install)."""
    frags = list(config_fragments) if config_fragments else None
    run_build_flow(
        version,
        source_dir,
        skip_install=True,
        dry_run=True,
        config_path=config_path,
        assume_yes_install=False,
        packages_output_dir=packages_output_dir,
        quiet_build=False,
        config_fragments=frags,
        use_llvm=False,
        localmodconfig=use_localmodconfig,
        force_rebuild=False,
    )


@cli.command("interactive")
@click.pass_context
def interactive(ctx: click.Context) -> None:
    """Wizard: system snapshot (kernel, HW, modules) → deps → kernel list → install opt → build."""
    cfg = _load_config()
    paths = _paths(cfg)
    setup_logging(paths["logs"], **cfg.get("logging", {}))
    banner()

    total_steps = 5

    print_step(1, total_steps, "Current system — kernel, hardware, loaded modules")
    sc = SystemChecker()
    snapshot = sc.get_interactive_snapshot()
    print_interactive_snapshot(snapshot)

    vr = sc.validate_environment()
    print_table(
        "Quick validation",
        [
            {"item": "Debian-based", "ok": str(sc.is_debian_based())},
            {
                "item": "Disk space (>= 20 GB target)",
                "ok": str(vr.system_info.get("hardware", {}).get("disk", {}).get("ok", "")),
            },
            {
                "item": "RAM / swap",
                "ok": str(vr.system_info.get("hardware", {}).get("memory", {}).get("ram_ok", "")),
            },
        ],
        ["item", "ok"],
    )
    for w in vr.warnings:
        click.echo(click.style(f"Warning: {w}", fg="yellow"))
    if vr.errors:
        for e in vr.errors:
            click.echo(click.style(f"Error: {e}", fg="red"))
        if not confirm("Continue anyway? (not recommended)", default=False):
            return
    elif not confirm(
        "Continue with kernel selection and build steps?",
        default=True,
    ):
        click.echo("Cancelled.")
        return

    print_step(2, total_steps, "Build dependencies")
    _ensure_build_dependencies(cfg, logging.getLogger("getkernel"))

    print_step(3, total_steps, "Choose kernel version (kernel.org)")
    fetcher = _make_fetcher(cfg, paths["cache"])
    try:
        data = fetcher.fetch_kernel_versions()
    except GetKernelError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)
    versions = data.get("versions", [])[:25]
    if not versions:
        click.echo("No kernel versions returned from kernel.org.", err=True)
        sys.exit(1)
    rows = []
    for i, v in enumerate(versions, start=1):
        rows.append(
            {
                "#": str(i),
                "version": v.get("version", ""),
                "type": v.get("type", ""),
                "released": v.get("released", ""),
            }
        )
    print_table("Available kernels (kernel.org)", rows, ["#", "version", "type", "released"])
    try:
        idx = prompt_kernel_selection(len(rows))
    except ValueError:
        click.echo(click.style("Invalid selection.", fg="red"), err=True)
        sys.exit(1)
    if idx is None:
        click.echo(click.style("Aborted.", fg="yellow"))
        return
    ver = versions[idx]["version"]

    print_step(4, total_steps, "After build: install generated .deb packages")
    skip_install = not confirm(
        "After the build finishes, install the generated .deb packages to this system?",
        default=True,
    )

    print_step(5, total_steps, "Confirm build")
    print_build_step_summary(ver)
    if not confirm("Start the build now?", default=False):
        click.echo("Cancelled.")
        return

    install_yes = bool(ctx.obj.get("assume_yes")) or assume_yes_from_env()
    run_build_flow(
        ver,
        None,
        skip_install,
        assume_yes_install=install_yes,
        force_rebuild=False,
    )


def main() -> None:
    ensure_elevated()
    try:
        cli()
    except GetKernelError as exc:
        print_error_block(type(exc).__name__, str(exc), None)
        sys.exit(1)


if __name__ == "__main__":
    main()
