"""Tests for kernel module grouping heuristics."""

from utils.module_groups import classify_kernel_module, group_kernel_modules


def test_classify_known() -> None:
    assert classify_kernel_module("nvidia") == "Graphics / GPU"
    assert classify_kernel_module("nf_conntrack") == "Network stack / netfilter"
    assert classify_kernel_module("snd_hda_intel") == "Sound"
    assert classify_kernel_module("vboxdrv") == "Virtualization"
    assert classify_kernel_module("ext4") == "Filesystems"


def test_group_order_and_sort() -> None:
    names = ["nf_nat", "nf_conntrack", "amdgpu"]
    groups = group_kernel_modules(names)
    cats = [c for c, _ in groups]
    assert "Graphics / GPU" in cats
    assert "Network stack / netfilter" in cats
    # alphabetically within category
    net = next(m for c, m in groups if c == "Network stack / netfilter")
    assert net == ["nf_conntrack", "nf_nat"]


