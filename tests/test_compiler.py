"""Compiler target resolution."""

import tempfile
import unittest
from pathlib import Path

from modules.compiler import Compiler


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
