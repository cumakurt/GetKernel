"""Heuristic grouping of loaded kernel module names for display."""

from __future__ import annotations

from collections import defaultdict
from typing import DefaultDict, List, Tuple


# Display order (categories not in this list appear after, sorted by name).
CATEGORY_ORDER: Tuple[str, ...] = (
    "Graphics / GPU",
    "Sound",
    "Network stack / netfilter",
    "Wireless",
    "Storage / block",
    "Virtualization",
    "Filesystems",
    "Crypto / security",
    "Bluetooth",
    "USB / HID",
    "Security / integrity",
    "CPU / ACPI / power",
    "Hardware / vendor drivers",
    "Miscellaneous",
)


def classify_kernel_module(name: str) -> str:
    """Assign a single category label to a module name (best-effort heuristics)."""
    n = name.lower()

    if any(
        n.startswith(p)
        for p in (
            "vbox",
            "kvm",
            "virtio",
            "xen",
            "hyperv",
            "vmw",
        )
    ):
        return "Virtualization"

    if (
        "nvidia" in n
        or n.startswith(("nouveau", "amdgpu", "radeon", "i915", "drm", "gpu"))
        or n.endswith("_drm")
    ):
        return "Graphics / GPU"

    if n.startswith(("snd_", "soundwire")) or n in ("snd", "soundcore"):
        return "Sound"

    if any(
        n.startswith(p)
        for p in (
            "cfg80211",
            "mac80211",
            "iwlwifi",
            "iwlmvm",
            "iwldvm",
            "ath",
            "rt2x00",
            "rtw",
            "brcmfmac",
            "brcmsmac",
            "mwifiex",
            "mt7",
            "mt76",
            "rtl",
            "wl",
        )
    ):
        return "Wireless"

    if any(
        n.startswith(p)
        for p in (
            "nf_",
            "nft_",
            "ipt_",
            "ip6_tables",
            "ip_tables",
            "iptable_",
            "xt_",
            "ip_set",
            "bridge",
            "veth",
            "bond",
            "vlan",
            "llc",
            "stp",
            "netfilter",
            "nfnetlink",
            "nf_nat",
            "nf_conntrack",
            "nf_defrag",
            "nf_log",
            "nf_tables",
            "nft_",
            "tcp_diag",
            "udp_diag",
            "inet_diag",
            "x_tables",
            "xfrm",
            "can",
            "qrtr",
            "netlink",
            "vxlan",
            "geneve",
            "openvswitch",
            "tipc",
        )
    ):
        return "Network stack / netfilter"

    if any(
        n.startswith(p)
        for p in (
            "nvme",
            "scsi_mod",
            "sd_mod",
            "sr_mod",
            "dm_",
            "loop",
            "nbd",
            "mtd",
            "mmc_",
            "usb_storage",
            "uas",
            "raid",
            "md_",
        )
    ):
        return "Storage / block"

    if any(
        n.startswith(p)
        for p in (
            "ext4",
            "xfs",
            "btrfs",
            "vfat",
            "ntfs",
            "nfs",
            "overlay",
            "fuse",
            "nls_",
            "fat",
            "jbd",
            "mbcache",
            "fscrypto",
            "squashfs",
            "isofs",
            "udf",
        )
    ):
        return "Filesystems"

    if any(
        n.startswith(p)
        for p in (
            "crypto",
            "aes",
            "sha",
            "gf128",
            "gcm",
            "ccm",
            "chacha",
            "poly1305",
            "algif",
            "cmac",
            "ecb",
            "cbc",
            "ctr",
        )
    ):
        return "Crypto / security"

    if n.startswith(("bluetooth", "btusb", "btrtl", "btintel", "btbcm", "rfcomm", "bnep", "hidp")):
        return "Bluetooth"

    if n.startswith(("usb", "uas", "hid_", "uhci", "ehci", "xhci")):
        return "USB / HID"

    if any(
        n.startswith(p)
        for p in (
            "selinux",
            "apparmor",
            "integrity",
            "tpm",
            "trusted",
            "evm",
            "ima",
            "key",
            "encrypted",
        )
    ):
        return "Security / integrity"

    if any(
        n.startswith(p)
        for p in (
            "acpi",
            "thermal",
            "fan",
            "cpufreq",
            "cpuid",
            "intel_rapl",
            "intel_power",
            "amd_",
            "k10temp",
            "coretemp",
            "msr",
            "idle",
        )
    ):
        return "CPU / ACPI / power"

    if any(
        n.startswith(p)
        for p in (
            "intel_",
            "i2c_",
            "spi_",
            "mei_",
            "ipmi",
            "lpc_",
            "pinctrl",
            "gpio",
            "regmap",
            "mfd",
            "pwm",
            "leds_",
            "pps_",
            "pps_core",
        )
    ):
        return "Hardware / vendor drivers"

    return "Miscellaneous"


def group_kernel_modules(names: List[str]) -> List[Tuple[str, List[str]]]:
    """
    Group module names by category.
    Returns ordered list of (category, sorted modules in category).
    """
    buckets: DefaultDict[str, List[str]] = defaultdict(list)
    for name in names:
        cat = classify_kernel_module(name)
        buckets[cat].append(name)

    for cat in buckets:
        buckets[cat].sort(key=str.lower)

    seen = set()
    ordered: List[Tuple[str, List[str]]] = []
    for cat in CATEGORY_ORDER:
        if cat in buckets and buckets[cat]:
            ordered.append((cat, buckets[cat]))
            seen.add(cat)

    for cat in sorted(buckets.keys()):
        if cat not in seen and buckets[cat]:
            ordered.append((cat, buckets[cat]))

    return ordered
