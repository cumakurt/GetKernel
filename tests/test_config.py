"""Tests for config_manager helpers."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_prepare_external_module_config_normalizes_release_and_modules(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".config").write_text(
                '\n'.join(
                    (
                        'CONFIG_LOCALVERSION="-distro"',
                        "CONFIG_LOCALVERSION_AUTO=y",
                        "# CONFIG_MODULES is not set",
                        "# CONFIG_MODULE_UNLOAD is not set",
                        "CONFIG_PRINTK=y",
                        "CONFIG_TRIM_UNUSED_KSYMS=y",
                        'CONFIG_SYSTEM_TRUSTED_KEYS="debian/missing.pem"',
                        "",
                    )
                ),
                encoding="utf-8",
            )
            cm = ConfigManager(str(root))
            with patch.object(cm, "run_oldconfig", return_value=True):
                cleared = cm.prepare_external_module_config("")

            text = (root / ".config").read_text(encoding="utf-8")
            self.assertIn('CONFIG_LOCALVERSION=""', text)
            self.assertIn("# CONFIG_LOCALVERSION_AUTO is not set", text)
            self.assertIn("CONFIG_MODULES=y", text)
            self.assertIn("CONFIG_MODULE_UNLOAD=y", text)
            self.assertIn("# CONFIG_TRIM_UNUSED_KSYMS is not set", text)
            self.assertIn('CONFIG_SYSTEM_TRUSTED_KEYS=""', text)
            self.assertEqual(cleared, ["CONFIG_SYSTEM_TRUSTED_KEYS"])
            self.assertEqual(cm.validate_config(), (True, []))

    def test_validate_config_checks_value_not_only_key_presence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".config").write_text(
                "# CONFIG_MODULES is not set\nCONFIG_MODULE_UNLOAD=y\nCONFIG_PRINTK=y\n",
                encoding="utf-8",
            )
            ok, errors = ConfigManager(str(root)).validate_config()
            self.assertFalse(ok)
            self.assertTrue(any("CONFIG_MODULES must be y" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
