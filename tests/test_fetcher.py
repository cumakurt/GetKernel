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

        data = self.fetcher.fetch_kernel_versions()
        self.assertIn("versions", data)
        self.assertTrue(len(data["versions"]) >= 1)
        self.assertEqual(data.get("stable"), "6.12.0")


if __name__ == "__main__":
    unittest.main()
