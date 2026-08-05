"""Tests for kernel_fetcher (mocked network)."""

import json
import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import requests

from modules.kernel_fetcher import KernelFetcher
from utils.exceptions import VerificationError


class TestKernelFetcher(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.fetcher = KernelFetcher(cache_dir=self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @patch("modules.kernel_fetcher.requests.Session.get")
    def test_fetch_kernel_versions_parses_json(self, mock_get: MagicMock) -> None:
        sample = {
            "latest_stable": {"version": "6.12.0"},
            "releases": [
                {
                    "moniker": "stable",
                    "version": "6.12.0",
                    "released": {"isodate": "2024-01-01"},
                    "source": "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.12.0.tar.xz",
                    "pgp": "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.12.0.tar.sign",
                },
                {
                    "moniker": "mainline",
                    "version": "6.13-rc1",
                    "released": {"isodate": "2024-02-01"},
                    "source": "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.13-rc1.tar.xz",
                },
                {
                    "moniker": "mainline",
                    "version": "6.13-beta1",
                    "released": {"isodate": "2024-02-02"},
                    "source": "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.13-beta1.tar.xz",
                },
                {
                    "moniker": "longterm",
                    "version": "6.6.30",
                    "released": {"isodate": "2024-01-15"},
                    "source": "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.6.30.tar.xz",
                },
            ],
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = sample
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        data = self.fetcher.fetch_kernel_versions(include_beta=False, include_rc=False)
        versions = [v["version"] for v in data["versions"]]
        self.assertIn("6.12.0", versions)
        self.assertNotIn("6.13-rc1", versions)
        self.assertNotIn("6.13-beta1", versions)
        self.assertEqual(data.get("stable"), "6.12.0")
        stable = next(v for v in data["versions"] if v["version"] == "6.12.0")
        self.assertEqual(
            stable["pgp_url"],
            "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.12.0.tar.sign",
        )

    def test_from_config_respects_flags(self) -> None:
        fetcher = KernelFetcher.from_config(
            "/tmp/getkernel-test-cache",
            {
                "verify_checksum": False,
                "verify_signature": True,
                "include_beta": False,
                "include_rc": False,
            },
        )
        self.assertFalse(fetcher.verify_checksum_enabled)
        self.assertTrue(fetcher.verify_signature_enabled)
        self.assertFalse(fetcher.include_beta)
        self.assertFalse(fetcher.include_rc)

    def test_mirror_urls_include_all_cdns(self) -> None:
        urls = self.fetcher._mirror_urls(
            "6.1.0",
            "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.0.tar.xz",
        )
        self.assertGreaterEqual(len(urls), 2)

    def test_noncanonical_snapshot_is_not_mixed_with_xz_mirrors(self) -> None:
        snapshot = "https://git.kernel.org/torvalds/t/linux-7.0-rc1.tar.gz"
        self.assertEqual(self.fetcher._mirror_urls("7.0-rc1", snapshot), [snapshot])

    def test_latest_helpers_use_normalized_cache(self) -> None:
        self.fetcher._releases_cache = {
            "stable": "6.12.0",
            "mainline": "6.13-rc1",
            "longterm": ["6.6.1"],
            "versions": [],
        }
        self.assertEqual(self.fetcher.get_latest_stable(), "6.12.0")
        self.assertEqual(self.fetcher.get_latest_mainline(), "6.13-rc1")
        self.assertEqual(self.fetcher.get_longterm_versions(), ["6.6.1"])

    def test_signature_url_targets_uncompressed_tar_signature(self) -> None:
        self.assertEqual(
            self.fetcher._signature_url(
                "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.0.tar.xz"
            ),
            "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.0.tar.sign",
        )

    def test_network_failure_falls_back_to_stale_release_cache(self) -> None:
        cached = {
            "latest_stable": {"version": "6.12.0"},
            "releases": [
                {
                    "moniker": "stable",
                    "version": "6.12.0",
                    "source": "https://cdn.kernel.org/linux-6.12.0.tar.xz",
                }
            ],
        }
        cache_path = self.fetcher._release_cache_path
        cache_path.write_text(json.dumps(cached), encoding="utf-8")
        old = time.time() - self.fetcher.RELEASE_CACHE_TTL_SEC - 10
        os.utime(cache_path, (old, old))
        with patch.object(
            self.fetcher.session,
            "get",
            side_effect=requests.ConnectionError("offline"),
        ):
            data = self.fetcher.fetch_kernel_versions()
        self.assertEqual(data["stable"], "6.12.0")

    def test_checksum_verification_fails_closed_when_entry_is_missing(self) -> None:
        with self.assertRaises(VerificationError):
            self.fetcher._assert_checksum_available(None, "linux-7.0-rc1.tar.gz")


if __name__ == "__main__":
    unittest.main()
