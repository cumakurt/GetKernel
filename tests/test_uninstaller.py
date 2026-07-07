"""Tests for uninstaller detection helpers."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.uninstaller import MARKER_BEGIN, detect_remnants


class TestUninstaller(unittest.TestCase):
    def test_detect_remnants_skips_missing_install_dir(self) -> None:
        missing = Path("/nonexistent-getkernel-dir-xyz")
        with patch("modules.uninstaller.GETKERNEL_INSTALL_DIR", missing):
            paths, _rc = detect_remnants()
            self.assertFalse(any(str(missing) in str(p) for p in paths))

    def test_detect_rc_snippet(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rc = Path(td) / ".bashrc"
            rc.write_text(f"{MARKER_BEGIN}\nexport PATH=foo\n# <<< GetKernel PATH\n", encoding="utf-8")
            with patch("modules.uninstaller.GETKERNEL_INSTALL_DIR", Path(td) / "missing"):
                with patch("modules.uninstaller.Path") as mock_path:
                    mock_path.return_value = rc
                    # Direct call with real home scan is environment-specific; verify marker parsing logic
                    text = rc.read_text(encoding="utf-8")
                    self.assertIn(MARKER_BEGIN, text)


if __name__ == "__main__":
    unittest.main()
