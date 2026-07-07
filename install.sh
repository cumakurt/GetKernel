#!/usr/bin/env bash
# Install GetKernel under /usr/local/getkernel and expose getkernel on PATH.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/usr/local/getkernel"
VENV_DIR="$INSTALL_DIR/.venv"
GETKERNEL_BIN="$VENV_DIR/bin/getkernel"
MARKER_BEGIN="# >>> GetKernel PATH (added by install.sh)"
MARKER_END="# <<< GetKernel PATH"

REMNANT_PATHS=()
REMNANT_RC_FILES=()

# ── UI (colors disabled when not a TTY or NO_COLOR is set) ──────────────────
_R='' _B='' _D='' _CY='' _GR='' _YL='' _RD='' _MG=''
if [[ -t 1 ]] && [[ -z "${NO_COLOR:-}" ]]; then
  _R=$'\033[0m'
  _B=$'\033[1m'
  _D=$'\033[2m'
  _CY=$'\033[36m'
  _GR=$'\033[32m'
  _YL=$'\033[33m'
  _RD=$'\033[31m'
  _MG=$'\033[35m'
fi

ui_header() {
  echo ""
  echo "${_CY}${_B}  GetKernel${_R} ${_D}installer${_R}"
  echo "${_D}  ─────────────────────────────────────${_R}"
  echo ""
}

ui_section() {
  echo "${_B}$1${_R}"
}

ui_ok() {
  echo "  ${_GR}✓${_R} $1"
}

ui_warn() {
  echo "  ${_YL}!${_R} $1"
}

ui_err() {
  echo "${_RD}✗${_R} $1" >&2
}

ui_dim() {
  echo "  ${_D}$1${_R}"
}

ui_item() {
  echo "    ${_D}·${_R} $1"
}

ui_done() {
  echo ""
  echo "${_GR}${_B}  Done${_R}"
  echo "${_D}  ─────────────────────────────────────${_R}"
  echo ""
  echo "  ${_B}Run:${_R}  ${_CY}getkernel --help${_R}"
  ui_dim "Install root: $INSTALL_DIR"
  echo ""
}

ui_prompt() {
  local msg=$1
  if [[ ! -t 0 ]] && [[ -r /dev/tty ]]; then
    echo -ne "  ${_YL}?${_R} ${msg} " >/dev/tty
    read -r -n 1 _reply </dev/tty || true
    echo "" >/dev/tty
  elif [[ -t 0 ]]; then
    echo -ne "  ${_YL}?${_R} ${msg} "
    read -r -n 1 _reply
    echo ""
  else
    return 1
  fi
  [[ "${_reply:-}" == "y" || "${_reply:-}" == "Y" ]]
}

usage() {
  ui_header
  echo "  Usage: ${0##*/} [options]"
  echo ""
  ui_dim "--dev            dev dependencies (pytest)"
  ui_dim "--recreate-venv  fresh virtualenv"
  ui_dim "--no-symlink     skip /usr/local/bin/getkernel"
  ui_dim "--yes            remove old files without prompt"
  echo ""
  ui_dim "Requires: sudo ./install.sh"
  echo ""
  exit "${1:-0}"
}

# ── Options ─────────────────────────────────────────────────────────────────
DEV=0
RECREATE=0
NO_SYMLINK=0
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --dev) DEV=1 ;;
    --recreate-venv) RECREATE=1 ;;
    --no-symlink) NO_SYMLINK=1 ;;
    --yes | -y) ASSUME_YES=1 ;;
    -h | --help) usage 0 ;;
    *)
      ui_err "Unknown option: $arg"
      usage 1
      ;;
  esac
done

if [[ $(id -u) -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    ui_warn "Administrator privileges required — switching to sudo …"
    exec sudo -E "$ROOT/install.sh" "$@"
  fi
  ui_err "Run as root: sudo \"$ROOT/install.sh\""
  exit 1
fi

ui_header

# ── Prerequisites ───────────────────────────────────────────────────────────
ui_section "Prerequisites"

if ! command -v python3 >/dev/null 2>&1; then
  ui_err "python3 not found"
  exit 1
fi

python3 - <<'PY' || exit 1
import sys
if sys.version_info < (3, 8):
    sys.stderr.write("Error: Python 3.8 or newer is required.\n")
    sys.exit(1)
PY

ui_ok "Python $(python3 -V 2>&1 | cut -d' ' -f2)"

# ── Remnant detection / cleanup ─────────────────────────────────────────────
_array_append_unique() {
  local -n _arr=$1
  local item="$2"
  local existing
  for existing in "${_arr[@]:-}"; do
    [[ "$existing" == "$item" ]] && return 0
  done
  _arr+=("$item")
}

_is_getkernel_symlink() {
  local p="$1"
  [[ -L "$p" ]] || return 1
  local target
  target=$(readlink "$p" 2>/dev/null || true)
  [[ "$target" == *getkernel* ]] || [[ "$target" == *GetKernel* ]] || [[ "$target" == *".venv/bin/getkernel"* ]]
}

detect_remnants() {
  REMNANT_PATHS=()
  REMNANT_RC_FILES=()

  [[ -e "$INSTALL_DIR" ]] && _array_append_unique REMNANT_PATHS "$INSTALL_DIR"
  [[ -e /usr/local/bin/getkernel ]] && _array_append_unique REMNANT_PATHS "/usr/local/bin/getkernel"
  [[ -e /usr/bin/getkernel ]] && _is_getkernel_symlink /usr/bin/getkernel \
    && _array_append_unique REMNANT_PATHS "/usr/bin/getkernel"

  local home rc
  for home in /root /home/*; do
    [[ -d "$home" ]] || continue
    if [[ -e "$home/.local/bin/getkernel" ]] && _is_getkernel_symlink "$home/.local/bin/getkernel"; then
      _array_append_unique REMNANT_PATHS "$home/.local/bin/getkernel"
    fi
    for rc in .profile .bashrc .zshrc; do
      if [[ -f "$home/$rc" ]] && grep -qF "$MARKER_BEGIN" "$home/$rc" 2>/dev/null; then
        _array_append_unique REMNANT_RC_FILES "$home/$rc"
      fi
    done
  done
}

remove_path_snippet() {
  local f="$1"
  local tmp
  tmp=$(mktemp)
  awk -v begin="$MARKER_BEGIN" -v end="$MARKER_END" '
    $0 == begin { skip = 1; next }
    $0 == end { skip = 0; next }
    !skip { print }
  ' "$f" > "$tmp"
  mv "$tmp" "$f"
}

confirm_cleanup() {
  local total=$(( ${#REMNANT_PATHS[@]} + ${#REMNANT_RC_FILES[@]} ))
  [[ "$total" -eq 0 ]] && return 0

  echo ""
  ui_section "Previous installation"
  ui_warn "$total item(s) will be removed before continuing"
  local p f
  for p in "${REMNANT_PATHS[@]}"; do
    ui_item "$p"
  done
  for f in "${REMNANT_RC_FILES[@]}"; do
    ui_item "$f"
  done

  if [[ "$ASSUME_YES" -eq 1 ]]; then
    ui_dim "Confirmed (--yes)"
    return 0
  fi

  if [[ ! -t 0 ]] && [[ ! -r /dev/tty ]]; then
    ui_err "Remnants found — re-run with --yes (no interactive terminal)"
    exit 1
  fi

  if ui_prompt "Remove and continue? [y/N]"; then
    return 0
  fi

  ui_dim "Cancelled."
  exit 0
}

cleanup_remnants() {
  local p f
  for p in "${REMNANT_PATHS[@]}"; do
    rm -rf "$p"
  done
  for f in "${REMNANT_RC_FILES[@]}"; do
    remove_path_snippet "$f"
  done
}

sync_source() {
  mkdir -p "$INSTALL_DIR"

  local -a excludes=(
    --exclude='.git/'
    --exclude='.venv/'
    --exclude='venv/'
    --exclude='__pycache__/'
    --exclude='*.egg-info/'
    --exclude='.eggs/'
    --exclude='.pytest_cache/'
    --exclude='.mypy_cache/'
    --exclude='.ruff_cache/'
    --exclude='.cursor/'
    --exclude='.github/'
    --exclude='.idea/'
    --exclude='.vscode/'
    --exclude='data/cache/*'
    --exclude='data/logs/*'
    --exclude='data/builds/*'
    --exclude='data/packages/*'
  )

  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete "${excludes[@]}" "$ROOT/" "$INSTALL_DIR/" 2>/dev/null
  else
    ui_warn "rsync not found — using tar (may leave stale files)"
    tar -C "$ROOT" -cf - \
      --exclude='.git' \
      --exclude='.venv' \
      --exclude='venv' \
      --exclude='__pycache__' \
      --exclude='*.egg-info' \
      --exclude='.eggs' \
      --exclude='.pytest_cache' \
      --exclude='.mypy_cache' \
      --exclude='.ruff_cache' \
      --exclude='.cursor' \
      --exclude='.github' \
      --exclude='.idea' \
      --exclude='.vscode' \
      --exclude='data/cache' \
      --exclude='data/logs' \
      --exclude='data/builds' \
      --exclude='data/packages' \
      . 2>/dev/null | tar -C "$INSTALL_DIR" -xf - 2>/dev/null
  fi

  mkdir -p \
    "$INSTALL_DIR/data/cache" \
    "$INSTALL_DIR/data/logs" \
    "$INSTALL_DIR/data/builds" \
    "$INSTALL_DIR/data/packages"
}

detect_remnants
confirm_cleanup
if [[ ${#REMNANT_PATHS[@]} -gt 0 || ${#REMNANT_RC_FILES[@]} -gt 0 ]]; then
  cleanup_remnants
  ui_ok "Previous files removed"
fi

# ── Install ─────────────────────────────────────────────────────────────────
echo ""
ui_section "Install"

sync_source
date -u +"%Y-%m-%dT%H:%M:%SZ" >"$INSTALL_DIR/.getkernel_install"
ui_ok "Files → $INSTALL_DIR"

if [[ "$RECREATE" -eq 1 ]] && [[ -d "$VENV_DIR" ]]; then
  rm -rf "$VENV_DIR"
fi

if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR" >/dev/null 2>&1
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

python -m pip install -U pip setuptools wheel -q
cd "$INSTALL_DIR"
if [[ "$DEV" -eq 1 ]]; then
  pip install -e ".[dev]" -q
  ui_ok "Python environment (with dev tools)"
else
  pip install -e . -q
  ui_ok "Python environment"
fi

if [[ -f "$GETKERNEL_BIN" ]] && [[ ! -x "$GETKERNEL_BIN" ]]; then
  chmod +x "$GETKERNEL_BIN" || true
fi

if [[ "$NO_SYMLINK" -eq 1 ]]; then
  ui_dim "Symlink skipped (--no-symlink)"
  ui_done
  ui_dim "Activate: source \"$VENV_DIR/bin/activate\""
  exit 0
fi

ln -sf "$GETKERNEL_BIN" "/usr/local/bin/getkernel"
ui_ok "Command → /usr/local/bin/getkernel"

ui_done
