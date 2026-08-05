"""Kernel compilation orchestration."""

from __future__ import annotations

import multiprocessing
import os
import re
import signal
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Deque, Dict, List, Mapping, Optional

from utils.constants import COMPILATION_ERROR_HINTS
from utils.exceptions import CompilationError
from utils.helpers import project_root, run_cmd
from utils.validator import validate_kernel_release, validate_localversion

BUILD_TIMEOUT_SEC = 86400
PROGRESS_UPDATE_INTERVAL_SEC = 0.1
BUILD_LOG_TAIL_LINES = 500

PHASE_LABELS = {
    "starting": "Starting build",
    "preparing": "Preparing kernel tree",
    "compiling": "Compiling source files",
    "linking": "Linking kernel image",
    "modules": "Building kernel modules",
    "packaging": "Creating Debian packages",
    "finishing": "Finishing up",
}


@dataclass(frozen=True)
class BuildProgressSnapshot:
    phase: str
    phase_label: str
    percent: float
    activity: str
    elapsed_seconds: float
    eta_seconds: Optional[float]
    units_done: int


class CompilationProgress:
    """Rough live progress parsed from make stdout."""

    _APPROX_SOURCE_FILES = 25000

    _PHASE_RULES: List[tuple[re.Pattern[str], str]] = [
        (re.compile(r"dpkg-deb|bindeb-pkg|\.deb\b|debian/rules|dh_", re.I), "packaging"),
        (re.compile(r"Building modules|MODPOST|__modpost|\bLD \[M\]", re.I), "modules"),
        (re.compile(r"\bLD\b.*vmlinux|LINK\s|\bvmlinux\b|bzImage|\bImage:", re.I), "linking"),
        (re.compile(r"Syncconfig|HOSTCC|HOSTLD|GEN\s|scripts/kconfig", re.I), "preparing"),
    ]

    def __init__(self, source_dir: Path, started_at: Optional[float] = None):
        self.source_dir = source_dir
        self.total_files = self._APPROX_SOURCE_FILES
        self.compiled_files = 0
        self.phase = "starting"
        self.activity = "Waiting for make output …"
        self.started_at = started_at or time.time()

    def update(self, log_line: str) -> None:
        stripped = log_line.strip()
        if not stripped:
            return

        for pattern, phase in self._PHASE_RULES:
            if pattern.search(stripped):
                self.phase = phase
                break

        if re.search(r"\b(CC|LD|AS|AR)\b", stripped):
            self.compiled_files += 1
            if self.phase in ("starting", "preparing"):
                self.phase = "compiling"
            if re.search(r"\bLD \[M\]|MODPOST", stripped):
                self.phase = "modules"
            elif re.search(r"\bLD\b", stripped) and "vmlinux" in stripped.lower():
                self.phase = "linking"

        activity = self._format_activity(stripped)
        if activity:
            self.activity = activity

    @staticmethod
    def _format_activity(line: str) -> str:
        match = re.search(r"\b(CC|LD|AS|AR|HOSTCC|HOSTLD|GEN)\s+(\S+)", line)
        if match:
            tool, target = match.group(1), match.group(2)
            name = Path(target).name
            return f"{tool} {name}"
        if len(line) > 80:
            return line[:77] + "…"
        return line

    def get_percentage(self) -> float:
        if self.phase == "packaging":
            return min(100.0, 92.0 + (self.compiled_files % 50) * 0.15)
        if self.phase == "finishing":
            return 100.0
        return min(90.0, (self.compiled_files / self.total_files) * 90.0)

    def eta_seconds(self) -> Optional[float]:
        pct = self.get_percentage()
        if pct < 2.0:
            return None
        elapsed = max(0.0, time.time() - self.started_at)
        remaining = pct
        if remaining <= 0:
            return None
        return elapsed * (100.0 - pct) / pct

    def snapshot(self, *, final: bool = False) -> BuildProgressSnapshot:
        phase = "finishing" if final else self.phase
        pct = 100.0 if final else self.get_percentage()
        return BuildProgressSnapshot(
            phase=phase,
            phase_label=PHASE_LABELS.get(phase, phase.replace("_", " ").title()),
            percent=pct,
            activity=self.activity if not final else "Build complete",
            elapsed_seconds=max(0.0, time.time() - self.started_at),
            eta_seconds=None if final else self.eta_seconds(),
            units_done=self.compiled_files,
        )


class Compiler:
    """Run make bindeb-pkg or deb-pkg in a kernel tree."""

    TARGETS = {
        "deb": "deb-pkg",
        "bindeb": "bindeb-pkg",
    }

    def __init__(self, kernel_source_dir: str) -> None:
        self.source_dir = Path(kernel_source_dir)
        try:
            # Honour container/cgroup CPU affinity instead of over-subscribing
            # every host CPU visible through multiprocessing.cpu_count().
            self.cpu_count = len(os.sched_getaffinity(0)) or 1
        except (AttributeError, OSError):
            self.cpu_count = multiprocessing.cpu_count() or 1
        self.compilation_start_time: Optional[float] = None
        self.estimated_duration: Optional[float] = None
        # The complete output is always streamed to last_build_log_path. Keeping
        # only a diagnostic tail prevents multi-hour builds from consuming RAM
        # proportional to make's output volume.
        self._log_lines: Deque[str] = deque(maxlen=BUILD_LOG_TAIL_LINES)
        self._progress_helper: Optional[CompilationProgress] = None
        self._last_progress_emit = 0.0
        self.build_id: Optional[str] = None
        self.last_build_log_path: Optional[Path] = None
        self._ccache_env: Dict[str, str] = {}

    def resolve_make_package_target(self, target: str) -> tuple[str, bool]:
        t = (target or "bindeb-pkg").strip().lower()
        if t in self.TARGETS:
            t = self.TARGETS[t]
        if t not in ("deb-pkg", "bindeb-pkg"):
            t = "bindeb-pkg"
        overridden = False
        if t == "deb-pkg" and not (self.source_dir / ".git").is_dir():
            t = "bindeb-pkg"
            overridden = True
        return t, overridden

    def prepare_source(
        self,
        config_file: Optional[str] = None,
        *,
        resume: bool = False,
        local_version: str = "",
    ) -> bool:
        if config_file:
            src = Path(config_file)
            if src.is_file():
                dest = self.source_dir / ".config"
                dest.write_bytes(src.read_bytes())
        if resume and (self.source_dir / ".config").is_file():
            return True
        env = os.environ.copy()
        env.setdefault("TERM", "xterm")
        env["LOCALVERSION"] = local_version
        for target in ("olddefconfig", "prepare"):
            cp = run_cmd(
                ["make", target],
                cwd=self.source_dir,
                env=env,
                timeout=3600,
            )
            if cp.returncode != 0:
                output = (cp.stderr or cp.stdout or "")[-4000:]
                raise CompilationError(f"make {target} failed:\n{output}")
        return True

    def get_kernel_release(self, local_version: str = "") -> str:
        """Return the exact Kbuild release used for package/module paths."""
        if not validate_localversion(local_version):
            raise CompilationError(
                "Invalid LOCALVERSION; use an empty value or a suffix such as '-custom'."
            )
        env = os.environ.copy()
        env["LOCALVERSION"] = local_version
        cp = run_cmd(
            ["make", "-s", "kernelrelease"],
            cwd=self.source_dir,
            env=env,
            timeout=300,
        )
        release = cp.stdout.strip().splitlines()[-1] if cp.stdout.strip() else ""
        if cp.returncode != 0 or not validate_kernel_release(release):
            detail = (cp.stderr or cp.stdout or "unknown error")[-2000:]
            raise CompilationError(f"Cannot determine a safe kernel release: {detail}")
        return release

    def has_partial_build(self) -> bool:
        if not (self.source_dir / ".config").is_file():
            return False
        for pattern in ("*.o", "*.ko"):
            if any(self.source_dir.rglob(pattern)):
                return True
        return False

    def _append_build_line(
        self,
        line: str,
        log_file,
        verbose: bool,
        progress_callback: Optional[Callable[[BuildProgressSnapshot], None]],
    ) -> None:
        self._log_lines.append(line)
        log_file.write(line)
        if verbose:
            print(line, end="")
        if self._progress_helper:
            self._progress_helper.update(line)
            now = time.monotonic()
            if (
                progress_callback
                and now - self._last_progress_emit >= PROGRESS_UPDATE_INTERVAL_SEC
            ):
                progress_callback(self._progress_helper.snapshot())
                self._last_progress_emit = now

    @staticmethod
    def _terminate_process(proc: subprocess.Popen) -> None:
        """Stop make and its compiler children so cancellation leaves no orphan build."""
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (AttributeError, ProcessLookupError, PermissionError):
            proc.terminate()
        try:
            proc.wait(timeout=10)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (AttributeError, ProcessLookupError, PermissionError):
            proc.kill()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass

    def compile_kernel(
        self,
        target: str = "bindeb-pkg",
        jobs: Optional[int] = None,
        local_version: str = "",
        verbose: bool = False,
        progress_callback: Optional[Callable[[BuildProgressSnapshot], None]] = None,
        log_path: Optional[Path] = None,
        build_id: Optional[str] = None,
        use_llvm: bool = False,
        extra_env: Optional[Mapping[str, str]] = None,
    ) -> bool:
        make_target, overridden = self.resolve_make_package_target(target)
        if overridden:
            print(
                "Note: deb-pkg needs a full git kernel tree; building with "
                "bindeb-pkg for this extracted source (no .git).",
                file=sys.stderr,
            )
        if not validate_localversion(local_version):
            raise CompilationError(
                "Invalid LOCALVERSION; use an empty value or a suffix such as '-custom'."
            )
        if jobs is None:
            j = self.cpu_count
        else:
            if isinstance(jobs, bool):
                raise CompilationError("build.jobs must be a positive integer or null")
            try:
                j = int(jobs)
            except (TypeError, ValueError) as exc:
                raise CompilationError("build.jobs must be a positive integer or null") from exc
            if j < 1:
                raise CompilationError("build.jobs must be a positive integer or null")
        self.compilation_start_time = time.time()
        self.build_id = build_id
        self._progress_helper = CompilationProgress(
            self.source_dir, started_at=self.compilation_start_time
        )
        env = os.environ.copy()
        env.setdefault("KBUILD_BUILD_USER", "getkernel")
        env.setdefault("KBUILD_BUILD_HOST", "localhost")
        env.update(self._ccache_env)
        if extra_env:
            env.update(dict(extra_env))
        # Do not allow the caller's shell or extra_env to silently change the
        # package release after it has been calculated and recorded.
        env["LOCALVERSION"] = local_version
        if use_llvm:
            env["LLVM"] = "1"

        log_file = log_path or (project_root() / "data" / "logs" / "build.log")
        log_file.parent.mkdir(parents=True, exist_ok=True)
        self.last_build_log_path = log_file

        if progress_callback and self._progress_helper:
            progress_callback(self._progress_helper.snapshot())
            self._last_progress_emit = time.monotonic()

        cmd = ["make", f"-j{j}", make_target]
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=self.source_dir,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except OSError as exc:
            raise CompilationError(f"Cannot start kernel build: {exc}") from exc
        self._log_lines.clear()
        line_count = 0
        deadline = time.monotonic() + BUILD_TIMEOUT_SEC
        try:
            if proc.stdout:
                with open(log_file, "w", encoding="utf-8", buffering=1) as lf:
                    while True:
                        if time.monotonic() >= deadline:
                            self._terminate_process(proc)
                            raise CompilationError(
                                f"Build timed out after {BUILD_TIMEOUT_SEC // 3600} hours"
                            )
                        if proc.poll() is not None:
                            for line in proc.stdout:
                                self._append_build_line(
                                    line,
                                    lf,
                                    verbose,
                                    progress_callback,
                                )
                                line_count += 1
                            break
                        import select

                        ready, _, _ = select.select([proc.stdout], [], [], 1.0)
                        if not ready:
                            continue
                        line = proc.stdout.readline()
                        if not line:
                            if proc.poll() is not None:
                                break
                            continue
                        self._append_build_line(line, lf, verbose, progress_callback)
                        line_count += 1
        except BaseException:
            self._terminate_process(proc)
            raise
        try:
            rc = proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            self._terminate_process(proc)
            rc = proc.returncode if proc.returncode is not None else -1
        elapsed = time.time() - (self.compilation_start_time or time.time())
        self.estimated_duration = elapsed

        if rc == 0 and self._progress_helper and progress_callback:
            progress_callback(self._progress_helper.snapshot(final=True))

        summary = (
            f"Build log ({line_count} lines, {elapsed:.1f}s): {log_file}"
            + (f"  [build_id={build_id}]" if build_id else "")
        )
        if verbose:
            print(f"\n{summary}", file=sys.stderr)
        elif not progress_callback:
            print(summary, file=sys.stderr)

        if rc != 0:
            retained_lines = list(self._log_lines)
            tail = "".join(retained_lines[-80:])
            full_for_hint = "".join(retained_lines[-400:])
            hint = self.handle_compilation_error(full_for_hint)
            bid = f" [build_id={build_id}]" if build_id else ""
            msg = (
                f"Build failed (exit {rc}){bid}. Full log: {log_file}\n"
                f"--- Last output ---\n{tail}\n"
                f"--- Suggested fix ---\n{hint['description']}: {hint['solution']}"
            )
            raise CompilationError(msg)
        return True

    def compile_modules(self) -> bool:
        env = os.environ.copy()
        cp = run_cmd(
            ["make", f"-j{self.cpu_count}", "modules"],
            cwd=self.source_dir,
            env=env,
            timeout=86400,
        )
        return cp.returncode == 0

    def get_compilation_progress(self) -> float:
        if self._progress_helper:
            return self._progress_helper.get_percentage()
        return 0.0

    def estimate_compile_time(self) -> Optional[int]:
        if self.estimated_duration:
            return int(self.estimated_duration)
        return None

    def get_remaining_time(self) -> Optional[int]:
        if self._progress_helper:
            eta = self._progress_helper.eta_seconds()
            return int(eta) if eta is not None else None
        return None

    def handle_compilation_error(self, error_output: str) -> Dict[str, str]:
        for pattern, solution, desc in COMPILATION_ERROR_HINTS:
            if re.search(pattern, error_output, re.IGNORECASE):
                return {
                    "error_type": "build",
                    "description": desc,
                    "solution": solution,
                }
        return {
            "error_type": "unknown",
            "description": "Build failed",
            "solution": "See log file for details.",
        }

    def clean_build(self, level: str = "normal") -> bool:
        targets = {"normal": "clean", "dist": "distclean", "mrproper": "mrproper"}
        tgt = targets.get(level, "clean")
        cp = run_cmd(["make", tgt], cwd=self.source_dir)
        return cp.returncode == 0

    def get_build_log(self) -> str:
        return "".join(self._log_lines)

    def enable_ccache(self) -> bool:
        ccache = Path("/usr/bin/ccache")
        if not ccache.is_file():
            return False
        self._ccache_env["PATH"] = f"/usr/lib/ccache:{os.environ.get('PATH', '')}"
        self._ccache_env["CC"] = "gcc"
        return True
