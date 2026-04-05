"""Misc helpers."""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Optional


def project_root() -> Path:
    """Directory containing GetKernel.py (repository root)."""
    return Path(__file__).resolve().parent.parent


def generate_build_id() -> str:
    """Short unique id for correlating logs and build artifacts."""
    return uuid.uuid4().hex[:12]


def assume_yes_from_env() -> bool:
    """Non-interactive install when GETKERNEL_ASSUME_YES is 1/true/yes."""
    return os.environ.get("GETKERNEL_ASSUME_YES", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def load_yaml_config(path: Path) -> dict:
    import yaml

    if not path.is_file():
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def merge_dict(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge_dict(out[k], v)
        else:
            out[k] = v
    return out


def resolve_path(base: Path, p: str) -> Path:
    path = Path(p)
    if path.is_absolute():
        return path
    return (base / path).resolve()


def kernel_major_branch(version: str) -> str:
    """Return vN.x segment for CDN paths (e.g. 6.8.1 -> v6.x)."""
    m = re.match(r"^(\d+)", version.strip())
    if not m:
        raise ValueError(f"Invalid kernel version: {version!r}")
    return f"v{m.group(1)}.x"


def source_tarball_name(version: str) -> str:
    return f"linux-{version}.tar.xz"


def cdn_source_url(version: str, mirror: str) -> str:
    branch = kernel_major_branch(version)
    name = source_tarball_name(version)
    return f"{mirror.rstrip('/')}/{branch}/{name}"


def run_cmd(
    args: list,
    cwd: Optional[Path] = None,
    env: Optional[dict] = None,
    timeout: Optional[float] = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def which(cmd: str) -> Optional[str]:
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = Path(d) / cmd
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def is_root() -> bool:
    return os.geteuid() == 0


def sudo_prefix() -> list:
    if is_root():
        return []
    sudo = which("sudo")
    if sudo:
        return [sudo]
    return []


def needs_elevation(argv: list[str]) -> bool:
    """
    Return True when the invoked command should run as root (sudo).
    Help/version and read-only commands stay unprivileged.
    """
    # Only treat global help/version as unprivileged (first token), not e.g. `build --version 6.1`.
    if argv and argv[0] in ("-h", "--help", "--version"):
        return False
    if not argv:
        return True
    first = argv[0]
    if first in ("check", "list", "about"):
        return False
    if first == "deps":
        return "--install" in argv
    if first in ("interactive", "build", "prepare", "cleanup"):
        return True
    return False


def ensure_elevated(argv: list[str] | None = None) -> None:
    """
    If root privileges are required, ask the user and re-exec this process with sudo.
    Skips when already root, non-posix, or GETKERNEL_NO_ELEVATE=1 (development only).
    """
    import sys

    if os.environ.get("GETKERNEL_NO_ELEVATE", "").lower() in ("1", "true", "yes"):
        return
    if os.name != "posix":
        return
    try:
        if os.geteuid() == 0:
            return
    except AttributeError:
        return

    sudo = which("sudo")
    if not sudo:
        print(
            "GetKernel needs administrator privileges for this command.\n"
            "Install the 'sudo' package or run the tool as root (e.g. su -).",
            file=sys.stderr,
        )
        sys.exit(1)

    args = argv if argv is not None else sys.argv[1:]
    if not needs_elevation(list(args)):
        return

    print(
        "\nThis operation requires administrator privileges (sudo).\n"
        "You will be prompted for your password if needed.\n",
        file=sys.stderr,
    )
    if sys.stdin.isatty():
        try:
            r = input("Re-launch GetKernel with sudo now? [Y/n]: ").strip().lower()
        except EOFError:
            r = "y"
        if r and r not in ("y", "yes"):
            sys.exit(1)
    cmd = [sudo, sys.executable, sys.argv[0], *sys.argv[1:]]
    os.execvp(sudo, cmd)
