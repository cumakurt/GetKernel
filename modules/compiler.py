"""Kernel compilation orchestration."""

from __future__ import annotations

import multiprocessing
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional

from utils.constants import COMPILATION_ERROR_HINTS
from utils.exceptions import CompilationError
from utils.helpers import project_root, run_cmd


class CompilationProgress:
    """Rough progress from make stdout lines."""

    # Fixed scale avoids scanning tens of thousands of *.c files at build start.
    _APPROX_SOURCE_FILES = 25000

    def __init__(self, source_dir: Path):
        self.source_dir = source_dir
        self.total_files = self._APPROX_SOURCE_FILES
        self.compiled_files = 0

    def update(self, log_line: str) -> None:
        if re.search(r"\b(CC|LD|AS|AR)\b", log_line):
            self.compiled_files += 1

    def get_percentage(self) -> float:
        return min(100.0, (self.compiled_files / self.total_files) * 100.0)


class Compiler:
    """Run make bindeb-pkg or deb-pkg in a kernel tree."""

    # Short aliases -> GNU make target names
    TARGETS = {
        "deb": "deb-pkg",
        "bindeb": "bindeb-pkg",
    }

    def __init__(self, kernel_source_dir: str) -> None:
        self.source_dir = Path(kernel_source_dir)
        self.cpu_count = multiprocessing.cpu_count() or 4
        self.compilation_start_time: Optional[float] = None
        self.estimated_duration: Optional[float] = None
        self._log_lines: List[str] = []
        self._progress_helper: Optional[CompilationProgress] = None
        self.build_id: Optional[str] = None
        self.last_build_log_path: Optional[Path] = None
        self._ccache_env: Dict[str, str] = {}

    def resolve_make_package_target(self, target: str) -> tuple[str, bool]:
        """
        Map config/CLI to a real make target.

        ``deb-pkg`` builds Debian *source* packages and recent kernels require a git
        checkout (scripts/Makefile.package: check-git). Tarballs from kernel.org are not
        git repos, so we fall back to ``bindeb-pkg`` (binary linux-image/linux-headers only).
        """
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

    def prepare_source(self, config_file: Optional[str] = None) -> bool:
        if config_file:
            src = Path(config_file)
            if src.is_file():
                dest = self.source_dir / ".config"
                dest.write_bytes(src.read_bytes())
        env = os.environ.copy()
        env.setdefault("TERM", "xterm")
        for target in ("olddefconfig", "prepare"):
            cp = subprocess.run(
                ["make", target],
                cwd=self.source_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=3600,
            )
            if cp.returncode != 0:
                raise CompilationError(f"make {target} failed:\n{cp.stderr[-4000:]}")
        return True

    def compile_kernel(
        self,
        target: str = "bindeb-pkg",
        jobs: Optional[int] = None,
        local_version: str = "-getkernel",
        verbose: bool = True,
        progress_callback: Optional[Callable[[float], None]] = None,
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
        j = jobs if jobs else self.cpu_count
        self.compilation_start_time = time.time()
        self.build_id = build_id
        self._progress_helper = CompilationProgress(self.source_dir)
        env = os.environ.copy()
        env["LOCALVERSION"] = local_version
        env.setdefault("KBUILD_BUILD_USER", "getkernel")
        env.setdefault("KBUILD_BUILD_HOST", "localhost")
        # Apply ccache env overrides (set by enable_ccache)
        env.update(self._ccache_env)
        if use_llvm:
            env["LLVM"] = "1"
        if extra_env:
            env.update(dict(extra_env))

        log_file = log_path or (project_root() / "data" / "logs" / "build.log")
        log_file.parent.mkdir(parents=True, exist_ok=True)
        self.last_build_log_path = log_file

        cmd = ["make", f"-j{j}", make_target]
        proc = subprocess.Popen(
            cmd,
            cwd=self.source_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._log_lines = []
        line_count = 0
        if proc.stdout:
            with open(log_file, "w", encoding="utf-8") as lf:
                for line in proc.stdout:
                    self._log_lines.append(line)
                    lf.write(line)
                    line_count += 1
                    if verbose:
                        print(line, end="")
                    if self._progress_helper:
                        self._progress_helper.update(line)
                        if progress_callback:
                            progress_callback(self._progress_helper.get_percentage())
        rc = proc.wait()
        elapsed = time.time() - (self.compilation_start_time or time.time())
        self.estimated_duration = elapsed
        summary = (
            f"Build log ({line_count} lines, {elapsed:.1f}s): {log_file}"
            + (f"  [build_id={build_id}]" if build_id else "")
        )
        if not verbose:
            print(summary, file=sys.stderr)
        else:
            print(f"\n{summary}", file=sys.stderr)
        if rc != 0:
            tail = "".join(self._log_lines[-80:])
            full_for_hint = "".join(self._log_lines[-400:])
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
        cp = subprocess.run(
            ["make", f"-j{self.cpu_count}", "modules"],
            cwd=self.source_dir,
            env=env,
            capture_output=True,
            text=True,
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
