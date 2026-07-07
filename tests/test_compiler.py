"""Compiler target resolution and build progress."""

import tempfile
import time
import unittest
from pathlib import Path

from modules.compiler import CompilationProgress, Compiler


class TestCompilationProgress(unittest.TestCase):
    def test_phase_compiling_on_cc(self) -> None:
        p = CompilationProgress(Path("/tmp/linux"), started_at=time.time())
        p.update("  CC      drivers/net/ethernet.o")
        snap = p.snapshot()
        self.assertEqual(snap.phase, "compiling")
        self.assertIn("ethernet", snap.activity)

    def test_phase_packaging(self) -> None:
        p = CompilationProgress(Path("/tmp/linux"), started_at=time.time())
        p.update("dpkg-deb: building package 'linux-image'")
        snap = p.snapshot()
        self.assertEqual(snap.phase, "packaging")

    def test_final_snapshot_is_complete(self) -> None:
        p = CompilationProgress(Path("/tmp/linux"), started_at=time.time())
        snap = p.snapshot(final=True)
        self.assertEqual(snap.percent, 100.0)
        self.assertEqual(snap.phase, "finishing")


class TestCompilerResolve(unittest.TestCase):
    def test_tarball_defaults_to_bindeb_without_git(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Makefile").write_text("all:\n", encoding="utf-8")
            c = Compiler(str(root))
            make_t, over = c.resolve_make_package_target("deb-pkg")
            self.assertEqual(make_t, "bindeb-pkg")
            self.assertTrue(over)

    def test_git_allows_deb_pkg(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Makefile").write_text("all:\n", encoding="utf-8")
            (root / ".git").mkdir()
            c = Compiler(str(root))
            make_t, over = c.resolve_make_package_target("deb-pkg")
            self.assertEqual(make_t, "deb-pkg")
            self.assertFalse(over)

    def test_compilation_error_hint_openssl(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = Compiler(str(td))
            out = "fatal error: openssl/foo.h: No such file"
            h = c.handle_compilation_error(out)
            self.assertEqual(h["error_type"], "build")
            self.assertIn("libssl-dev", h["solution"])


if __name__ == "__main__":
    unittest.main()
