"""Tests for config_manager helpers."""

import tempfile
import unittest
from pathlib import Path

from utils.exceptions import ConfigError

from modules.config_manager import ConfigManager


class TestConfigManager(unittest.TestCase):
    def test_get_config_diff(self) -> None:
        cm = ConfigManager("/tmp/nonexistent-linux")
        diff = cm.get_config_diff("CONFIG_A=y\n", "CONFIG_A=m\n")
        self.assertIn("removed", diff)
        self.assertIn("added", diff)

    def test_merge_fragments_requires_kernel_script(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".config").write_text("CONFIG_MODULES=y\n", encoding="utf-8")
            cm = ConfigManager(str(root))
            frag = root / "f.cfg"
            frag.write_text("# CONFIG_DUMMY=y\n", encoding="utf-8")
            with self.assertRaises(ConfigError) as ctx:
                cm.merge_config_fragments([frag])
            self.assertIn("merge_config.sh", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
