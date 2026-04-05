"""Shared constants."""

APP_VERSION = "1.0.0"

DEVELOPER_NAME = "Cuma KURT"
DEVELOPER_EMAIL = "cumakurt@gmail.com"
DEVELOPER_LINKEDIN_URL = "https://www.linkedin.com/in/cuma-kurt-34414917/"
DEVELOPER_GITHUB_REPO_URL = "https://github.com/cumakurt/GetKernel"

KERNEL_ORG_RELEASES_JSON = "https://www.kernel.org/releases.json"
CDN_MIRRORS = [
    "https://cdn.kernel.org/pub/linux/kernel",
    "https://mirrors.edge.kernel.org/pub/linux/kernel",
]

DEBIAN_BASED_IDS = (
    "debian",
    "ubuntu",
    "kali",
    "linuxmint",
    "pop",
    "elementary",
    "mx",
    "parrot",
    "zorin",
    "deepin",
)

REQUIRED_COMMANDS = (
    "dpkg",
    "apt-get",
    "gcc",
    "make",
    "ld",
    "tar",
    "xz",
)

# Prefer kernel-wedge (per plan section 2); fallback name handled in dependency_manager
REQUIRED_PACKAGES = [
    "build-essential",
    "gcc",
    "g++",
    "make",
    "libc6-dev",
    "libncurses-dev",
    "bison",
    "flex",
    "libssl-dev",
    "libelf-dev",
    "bc",
    "kmod",
    "cpio",
    "dwarves",
    "libdw-dev",
    "rsync",
    "dpkg-dev",
    "debhelper",
    "fakeroot",
    "kernel-wedge",
]

OPTIONAL_PACKAGES = [
    "ccache",
    "lz4",
    "zstd",
    "libzstd-dev",
]

COMPILATION_ERROR_HINTS = (
    (
        r"fatal error: openssl/.*\.h: No such file",
        "sudo apt-get install libssl-dev",
        "OpenSSL development headers missing",
    ),
    (
        r"fatal error: libelf\.h: No such file",
        "sudo apt-get install libelf-dev",
        "libelf development headers missing",
    ),
    (
        r"Unable to find the ncurses",
        "sudo apt-get install libncurses-dev",
        "ncurses library missing",
    ),
    (
        r"virtual memory exhausted|Cannot allocate memory",
        "Increase swap or reduce parallel jobs (-j)",
        "Insufficient memory",
    ),
    (
        r"bc: command not found",
        "sudo apt-get install bc",
        "bc calculator missing",
    ),
    (
        r"creating source package requires git repository|check-git",
        "Use bindeb-pkg (default in config) or clone linux.git; tarball trees cannot use deb-pkg.",
        "deb-pkg requires a git repository",
    ),
    (
        r"unmet build dependencies:.*libdw-dev|libdw-dev:native",
        "sudo apt-get install libdw-dev",
        "libdw-dev required for dpkg-buildpackage / bindeb-pkg",
    ),
    (
        r"LLVM[_ ]?=.*not found|clang: command not found|Cannot find suitable llvm",
        "Install clang and llvm (e.g. sudo apt-get install clang llvm) or build without --llvm",
        "LLVM/clang toolchain missing",
    ),
)
