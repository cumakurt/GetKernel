# GetKernel

[![CI](https://github.com/cumakurt/GetKernel/actions/workflows/ci.yml/badge.svg)](https://github.com/cumakurt/GetKernel/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

Build custom Linux kernel `.deb` packages on Debian-based systems: fetch from kernel.org, reuse your running kernel config, compile with live progress, and optionally install with backup and verification hooks.

<p align="center">
  <img src="img/1.png" alt="Wizard — system check and kernel selection" width="280" />
  <img src="img/2.png" alt="Download and live build progress" width="280" />
  <img src="img/3.png" alt="Build complete — install prompt" width="280" />
</p>

## Quick start

```bash
git clone https://github.com/cumakurt/GetKernel.git
cd GetKernel
sudo ./install.sh
sudo getkernel                    # interactive wizard (default)
# or a direct build:
sudo getkernel build --version 6.12.8
```

## Requirements

- Python 3.8+
- Debian, Ubuntu, Kali, or similar (dpkg/apt)
- Root or sudo for installs, builds, and package deployment

## Installation

System install (recommended):

```bash
sudo ./install.sh              # optional: --dev  --yes  --no-symlink  --recreate-venv
sudo ./uninstall.sh            # remove /usr/local/getkernel (or: sudo getkernel uninstall)
```

| Path | Purpose |
|------|---------|
| `/usr/local/getkernel` | Program files, config, virtualenv |
| `/usr/local/getkernel/data/cache` | Download cache |
| `/usr/local/getkernel/data/builds` | Kernel source trees and tarballs |
| `/usr/local/getkernel/data/logs` | Build logs (`build-<id>.log`) |
| `/usr/local/getkernel/data/packages/latest/` | Most recent `.deb` output |
| `/usr/local/getkernel/data/packages/archive/build-<id>/` | Archived builds |
| `/usr/local/bin/getkernel` | Symlink to the installed CLI |

### Install and update behavior

- **First install** — copies files to `/usr/local/getkernel` and creates the `getkernel` command.
- **In-place update** — if GetKernel is already at `/usr/local/getkernel`, the installer asks to update on top of the existing install. Program files are refreshed; **`data/cache`, `data/logs`, `data/builds`, and `data/packages` are preserved**.
- **Legacy paths** — installs under a different location (old symlinks, `~/.local/bin/getkernel`, PATH snippets pointing elsewhere) are listed separately. Removal of those files and their data requires **explicit confirmation**; you can skip cleanup and continue the new install.

Non-interactive: use `sudo ./install.sh --yes` to accept update and legacy-cleanup prompts.

Development install (local checkout):

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Commands

| Command | Purpose |
|---------|---------|
| `getkernel` / `interactive` | Step-by-step wizard (default) |
| `build` | Download, configure, compile, package; optional install |
| `prepare` | Source + config only (no compile) |
| `list` | Kernel versions from kernel.org (`--json`, `--no-rc`) |
| `check` | OS, disk, RAM, toolchain validation (`--json`) |
| `status` | Running kernel, GRUB, depot, last build log (`--json`) |
| `deps` | Missing build packages (`--install` to apt install) |
| `install` | Install `.deb` packages from depot (`--build-id`) |
| `packages list` | List latest and archived builds (`--json`) |
| `backups` | Boot file backups before install (`--json`) |
| `rollback` | Restore a backup by id |
| `cleanup` | Old kernel packages and/or build artifacts |
| `uninstall` | Remove GetKernel from `/usr/local/getkernel` |
| `about` | Project and author info |

Global flags: `--help`, `--version`, `--yes` / `-y` (auto-confirm install prompts).

Run `getkernel <command> --help` for full options.

## Common workflows

| Goal | Command |
|------|---------|
| Guided build | `sudo getkernel` |
| Full build + install prompt | `sudo getkernel build --version 6.12.8` |
| Build `.deb` only | `sudo getkernel build --version 6.12.8 --skip-install` |
| Prepare tree, no compile | `sudo getkernel prepare --version 6.12.8` |
| Non-interactive install | `sudo getkernel --yes build --version 6.12.8` |
| Custom `.config` | `sudo getkernel build --version 6.12.8 --config /path/.config` |
| Kconfig fragments | `sudo getkernel build --version 6.12.8 --fragment cfg1 --fragment cfg2` |
| Build profile | `--profile minimal`, `server`, or `desktop` |
| Interactive Kconfig | `--menuconfig` |
| Resume failed build | `--resume-build` |
| Trim to loaded modules | `--localmodconfig` |
| Clang/LLVM build | `--llvm` (install clang/llvm first) |
| Existing source tree | `--source-dir /path/to/linux-X.Y.Z` |
| Custom package output | `--output-dir /path/to/debs` |
| Force full rebuild | `--force-rebuild` |
| Install from depot | `sudo getkernel install` or `install --build-id <id>` |
| System status (JSON) | `getkernel status --json` |
| Roll back boot files | `sudo getkernel rollback backup-YYYYMMDD-HHMMSS` |
| Uninstall GetKernel | `sudo ./uninstall.sh` or `sudo getkernel uninstall` |
| Clean old kernels | `sudo getkernel cleanup --old-kernels` |
| Clean build junk | `sudo getkernel cleanup --build-artifacts` |

### Build terminal output

| Mode | Flag | Behavior |
|------|------|----------|
| Default | — | Live progress panel (phase, bar, ETA); full log in `data/logs/build-<id>.log` |
| Verbose | `--verbose` / `-v` | Stream all `make` output |
| Quiet | `--quiet` / `-q` | Minimal output; log file only |

`--quiet` and `--verbose` cannot be used together.

### Stored packages

If matching `.deb` files already exist under `data/packages/latest/`, `build` offers **rebuild** or **quit**. Install stored packages with **`getkernel install`** (or `install --build-id <id>` for archives under `data/packages/archive/`). The post-build install prompt appears only after a **fresh** build. Skip the rebuild check with `--force-rebuild` or when using `--config`, `--fragment`, `--profile`, `--menuconfig`, `--resume-build`, `--llvm`, `--localmodconfig`, or `--source-dir`.

After install, GetKernel verifies `/boot/vmlinuz-*` and `/lib/modules/*` when a kernel version hint is available.

## Privileges

| Activity | Privilege |
|----------|-----------|
| `check`, `list`, `status`, `packages list`, `backups`, `deps`, `about`, `--help` | Normal user |
| `build`, `prepare`, `install`, `deps --install`, `cleanup`, `rollback`, `uninstall`, wizard | **root / sudo** |

## Architecture

```mermaid
flowchart LR
  CLI[GetKernel CLI]
  KF[KernelFetcher]
  CM[ConfigManager]
  CO[Compiler]
  PB[PackageBuilder]
  PD[PackageDepot]
  IN[Installer]
  SA[SystemAdvisor]
  CLI --> KF
  CLI --> CM
  CLI --> CO
  CLI --> SA
  CO --> PB
  PB --> PD
  CLI --> IN
```

| Module | Role |
|--------|------|
| **KernelFetcher** | kernel.org metadata; download/resume; SHA256; optional GPG; CDN mirror fallback |
| **ConfigManager** | `.config` from running kernel or file; fragments; profiles; `menuconfig` |
| **Compiler** | `make bindeb-pkg` (default); live progress; resume partial builds |
| **PackageBuilder** | Collect `linux-*.deb` → `latest/`; archive per build id |
| **PackageDepot** | List and resolve packages from `latest/` and `archive/` |
| **Installer** | `dpkg`, `apt-get install -f`, initramfs, GRUB; backup/rollback; verify |
| **SystemAdvisor** | DKMS, GPU driver, and Secure Boot warnings before build/install |

Tarball trees without `.git` use **`bindeb-pkg`** automatically (`deb-pkg` needs a git checkout).

## Configuration

Copy `config/user_config.yaml.example` → `config/user_config.yaml` to override defaults from `config/default_config.yaml`.

| Key | Purpose |
|-----|---------|
| `paths.*` | cache, logs, builds, packages directories |
| `kernel.localversion` | suffix appended to kernel release |
| `kernel.reuse_downloads` | skip re-download when tarball/tree exists |
| `kernel.verify_checksum` / `kernel.verify_signature` | tarball SHA256 and optional GPG (`gpg` required) |
| `kernel.include_beta` / `kernel.include_rc` | filter kernel.org version list |
| `build.jobs` | parallel make jobs (`null` = CPU count) |
| `build.target` | `bindeb-pkg` or `deb-pkg` |
| `build.use_llvm` / `build.localmodconfig` | LLVM build; module trimming |
| `build.config_fragments` | Kconfig fragment paths |
| `build.profiles.*` | named profiles for `--profile` (`config/profiles/`) |
| `dependencies.auto_install` | apt install missing build deps before build |

## Environment variables

| Variable | Effect |
|----------|--------|
| `GETKERNEL_ASSUME_YES=1` | Auto-confirm install after build (like `--yes`) |
| `GETKERNEL_ROOT` | Override data/install root |
| `GETKERNEL_NO_ELEVATE=1` | Skip sudo re-exec (testing only) |

## Limitations & warnings

- No cross-compilation support; native toolchain only.
- Custom kernels can break **DKMS**, **NVIDIA**, and other out-of-tree drivers — especially on **RC/mainline** kernels. GetKernel warns before build/install when DKMS or proprietary drivers are detected.
- Replacing **`linux-libc-dev`** may affect userland builds on the same machine.
- **Secure Boot** may require extra steps for unsigned modules.
- **Back up** and know how to boot a previous kernel before installing. Use `getkernel backups` and `rollback` for boot-file recovery.

GetKernel modifies packages, `/boot`, initramfs, and GRUB. Use at your own risk; authors provide **no warranty**. See [SECURITY.md](SECURITY.md) for disclosures.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Contributions: [CONTRIBUTING.md](CONTRIBUTING.md)

## Author & license

**Cuma KURT** — [cumakurt@gmail.com](mailto:cumakurt@gmail.com) · [GitHub](https://github.com/cumakurt/GetKernel) · [LinkedIn](https://www.linkedin.com/in/cuma-kurt-34414917/)

Licensed under **GPL-3.0**.
