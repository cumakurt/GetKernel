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

# Recommended: interactive wizard (default when no command is given)
sudo getkernel

# Advanced: direct build without the wizard
sudo getkernel build --version 6.12.8
```

> **Note:** `sudo ./install.sh` installs the GetKernel **tool**. `sudo getkernel` (alone) starts the **kernel build wizard** — not tool installation.

## Recommended: interactive mode

After installing GetKernel, the easiest way to build a kernel is to run **no subcommand at all**:

```bash
sudo getkernel
```

This is equivalent to `sudo getkernel interactive` and is the **recommended entry point** for most users. The wizard walks you through:

1. System snapshot (running kernel, hardware, loaded modules)
2. Build dependency check (optional apt install)
3. Kernel version selection from kernel.org
4. Whether to install `.deb` packages after the build
5. Build confirmation and live progress

Use direct commands such as `getkernel build --version 6.12.8` only when you already know the version and flags you need (scripts, CI, or advanced workflows).

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
| `getkernel` / `interactive` | Step-by-step wizard (**default**, recommended) |
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

## Examples

### Tool install / uninstall

```bash
sudo ./install.sh
sudo ./install.sh --dev
sudo ./install.sh --yes
sudo ./install.sh --recreate-venv
sudo ./install.sh --no-symlink
sudo ./uninstall.sh
sudo getkernel uninstall -y
```

### General CLI

```bash
getkernel --help
getkernel --version
getkernel about
sudo getkernel --yes build --version 6.12.8
GETKERNEL_ASSUME_YES=1 sudo getkernel build --version 6.12.8
```

### Interactive wizard (recommended)

```bash
sudo getkernel
sudo getkernel interactive
```

### `check` — system validation

```bash
getkernel check
getkernel check --json
```

### `list` — kernel.org versions

```bash
getkernel list
getkernel list --no-rc
getkernel list --json
```

### `status` — system and depot overview

```bash
getkernel status
getkernel status --json
```

### `deps` — build dependencies

```bash
getkernel deps
sudo getkernel deps --install
```

### `build` — compile kernel packages

```bash
sudo getkernel build --version 6.12.8
sudo getkernel build --version 6.12.8 --skip-install
sudo getkernel --yes build --version 6.12.8
sudo getkernel build --version 6.12.8 --source-dir /usr/local/getkernel/data/builds/linux-6.12.8
sudo getkernel build --version 6.12.8 --config /path/to/.config
sudo getkernel build --version 6.12.8 --fragment config/fragments/my-tweaks.cfg
sudo getkernel build --version 6.12.8 --profile server
sudo getkernel build --version 6.12.8 --menuconfig
sudo getkernel build --version 6.12.8 --localmodconfig
sudo getkernel build --version 6.12.8 --llvm
sudo getkernel build --version 6.12.8 --resume-build
sudo getkernel build --version 6.12.8 --force-rebuild
sudo getkernel build --version 6.12.8 --output-dir /tmp/my-debs
sudo getkernel build --version 6.12.8 --dry-run
sudo getkernel build --version 6.12.8 --verbose
sudo getkernel build --version 6.12.8 --quiet
```

### `prepare` — source and config only

```bash
sudo getkernel prepare --version 6.12.8
sudo getkernel prepare --version 6.12.8 --config /path/.config
sudo getkernel prepare --version 6.12.8 --fragment cfg1 --localmodconfig
```

### `install` — install from package depot

```bash
sudo getkernel install
sudo getkernel --yes install
sudo getkernel install --build-id a1b2c3d4e5f6
sudo getkernel install --kernel-version 6.12.8-getkernel
```

### `packages list` — depot contents

```bash
getkernel packages list
getkernel packages list --json
```

### `backups` and `rollback`

```bash
getkernel backups
getkernel backups --json
sudo getkernel rollback backup-20260707-155257
ls /var/backups/getkernel/backup-20260707-155257/
cat /var/backups/getkernel/backup-20260707-155257/manifest.json
```

### `cleanup`

```bash
sudo getkernel cleanup --old-kernels
sudo getkernel cleanup --old-kernels --keep 3
sudo getkernel cleanup --old-kernels --dry-run
sudo getkernel cleanup --build-artifacts
sudo getkernel cleanup --old-kernels --build-artifacts
```

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

Example override:

```yaml
kernel:
  localversion: "-custom"
  reuse_downloads: true
  verify_checksum: true
  verify_signature: false
  include_beta: false
  include_rc: false

build:
  jobs: 8
  target: bindeb-pkg
  config_fragments:
    - config/fragments/my.cfg

dependencies:
  auto_install: true
  apt_update: true
```

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

```bash
GETKERNEL_ASSUME_YES=1 sudo getkernel build --version 6.12.8
GETKERNEL_ROOT=/tmp/gk-test getkernel status
GETKERNEL_NO_ELEVATE=1 getkernel check    # testing only
```

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
GETKERNEL_NO_ELEVATE=1 python3 GetKernel.py list --json
```

Contributions: [CONTRIBUTING.md](CONTRIBUTING.md)

## Author & license

**Cuma KURT** — [cumakurt@gmail.com](mailto:cumakurt@gmail.com) · [GitHub](https://github.com/cumakurt/GetKernel) · [LinkedIn](https://www.linkedin.com/in/cuma-kurt-34414917/)

Licensed under **GPL-3.0**.
