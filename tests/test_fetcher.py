"""Tests for kernel_fetcher (mocked network)."""

import unittest
from unittest.mock import MagicMock, patch

from modules.kernel_fetcher import KernelFetcher


class TestKernelFetcher(unittest.TestCase):
    def setUp(self) -> None:
        self.fetcher = KernelFetcher(cache_dir="/tmp/getkernel-test-cache")

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


if __name__ == "__main__":
    unittest.main()
