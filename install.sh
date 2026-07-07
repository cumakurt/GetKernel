#!/usr/bin/env bash
# Install GetKernel under /usr/local/getkernel and expose getkernel on PATH.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/usr/local/getkernel"
VENV_DIR="$INSTALL_DIR/.venv"
GETKERNEL_BIN="$VENV_DIR/bin/getkernel"
MARKER_BEGIN="# >>> GetKernel PATH (added by install.sh)"
MARKER_END="# <<< GetKernel PATH"

EXISTING_SAME_PATH=0
LEGACY_PATHS=()
LEGACY_RC_FILES=()

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
  if [[ "$EXISTING_SAME_PATH" -eq 1 ]]; then
    echo "${_GR}${_B}  Updated${_R}"
  else
    echo "${_GR}${_B}  Done${_R}"
  fi
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
  ui_dim "--yes            accept update / legacy cleanup without prompt"
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
    exec sudo "$ROOT/install.sh" "$@"
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
  _install_dir_from_symlink "$p" >/dev/null
}

_path_equal() {
  local a b
  a=$(realpath "$1" 2>/dev/null || echo "$1")
  b=$(realpath "$2" 2>/dev/null || echo "$2")
  [[ "$a" == "$b" ]]
}

_install_dir_from_symlink() {
  local link=$1
  local target root
  [[ -L "$link" ]] || return 1
  target=$(readlink "$link" 2>/dev/null || true)
  [[ -n "$target" ]] || return 1
  if [[ "$target" == *"/.venv/bin/getkernel" ]]; then
    root="${target%/.venv/bin/getkernel}"
    [[ -n "$root" ]] || return 1
    printf '%s\n' "$root"
    return 0
  fi
  return 1
}

_append_legacy_path() {
  local p=$1
  [[ -z "$p" ]] && return 0
  if [[ "$p" == "$INSTALL_DIR" ]] || _path_equal "$p" "$INSTALL_DIR"; then
    return 0
  fi
  _array_append_unique LEGACY_PATHS "$p"
}

detect_installation_state() {
  EXISTING_SAME_PATH=0
  LEGACY_PATHS=()
  LEGACY_RC_FILES=()

  if [[ -e "$INSTALL_DIR" ]] && {
    [[ -f "$INSTALL_DIR/.getkernel_install" ]] || [[ -f "$INSTALL_DIR/GetKernel.py" ]]
  }; then
    EXISTING_SAME_PATH=1
  fi

  local link dir home local_bin rc candidate
  for link in /usr/local/bin/getkernel /usr/bin/getkernel; do
    [[ -e "$link" ]] || continue
    if dir=$(_install_dir_from_symlink "$link" 2>/dev/null); then
      if ! _path_equal "$dir" "$INSTALL_DIR"; then
        _append_legacy_path "$dir"
        _append_legacy_path "$link"
      fi
    elif _is_getkernel_symlink "$link"; then
      _append_legacy_path "$link"
    fi
  done

  for home in /root /home/*; do
    [[ -d "$home" ]] || continue
    local_bin="$home/.local/bin/getkernel"
    if [[ -e "$local_bin" ]] && _is_getkernel_symlink "$local_bin"; then
      if dir=$(_install_dir_from_symlink "$local_bin" 2>/dev/null); then
        if ! _path_equal "$dir" "$INSTALL_DIR"; then
          _append_legacy_path "$dir"
          _append_legacy_path "$local_bin"
        fi
      else
        _append_legacy_path "$local_bin"
      fi
    fi
    for rc in .profile .bashrc .zshrc; do
      rc="$home/$rc"
      if [[ -f "$rc" ]] && grep -qF "$MARKER_BEGIN" "$rc" 2>/dev/null; then
        if grep -qF "$INSTALL_DIR" "$rc" 2>/dev/null; then
          continue
        fi
        _array_append_unique LEGACY_RC_FILES "$rc"
      fi
    done
  done

  for candidate in /opt/getkernel /usr/local/GetKernel; do
    if [[ -f "$candidate/.getkernel_install" ]] && ! _path_equal "$candidate" "$INSTALL_DIR"; then
      _append_legacy_path "$candidate"
    fi
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

confirm_same_path_update() {
  [[ "$EXISTING_SAME_PATH" -eq 1 ]] || return 0

  echo ""
  ui_section "Existing installation"
  ui_warn "GetKernel is already installed at $INSTALL_DIR"
  ui_dim "This run will update program files on top of the existing install."
  ui_dim "Your runtime data will be kept:"
  ui_item "data/cache"
  ui_item "data/logs"
  ui_item "data/builds"
  ui_item "data/packages"

  if [[ "$ASSUME_YES" -eq 1 ]]; then
    ui_dim "Update confirmed (--yes)"
    return 0
  fi

  if [[ ! -t 0 ]] && [[ ! -r /dev/tty ]]; then
    ui_err "Existing installation found — re-run with --yes (no interactive terminal)"
    exit 1
  fi

  if ui_prompt "Proceed with in-place update? [y/N]"; then
    return 0
  fi

  ui_dim "Cancelled."
  exit 0
}

confirm_legacy_cleanup() {
  local total=$(( ${#LEGACY_PATHS[@]} + ${#LEGACY_RC_FILES[@]} ))
  [[ "$total" -eq 0 ]] && return 1

  echo ""
  ui_section "Legacy installation (different path)"
  ui_warn "Found $total item(s) from an older or non-standard GetKernel install"
  ui_dim "These are not under $INSTALL_DIR and may be removed to avoid conflicts:"
  local p f
  for p in "${LEGACY_PATHS[@]}"; do
    ui_item "$p"
  done
  for f in "${LEGACY_RC_FILES[@]}"; do
    ui_item "$f (PATH snippet)"
  done

  if [[ "$ASSUME_YES" -eq 1 ]]; then
    ui_dim "Legacy cleanup confirmed (--yes)"
    return 0
  fi

  if [[ ! -t 0 ]] && [[ ! -r /dev/tty ]]; then
    ui_err "Legacy install paths found — re-run with --yes to remove them, or remove manually"
    return 1
  fi

  if ui_prompt "Remove legacy files and data? [y/N]"; then
    return 0
  fi

  ui_dim "Skipping legacy cleanup (continuing with install/update)."
  return 1
}

cleanup_legacy_paths() {
  local p f
  for p in "${LEGACY_PATHS[@]}"; do
    rm -rf "$p"
  done
  for f in "${LEGACY_RC_FILES[@]}"; do
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

detect_installation_state
confirm_same_path_update
if confirm_legacy_cleanup; then
  cleanup_legacy_paths
  ui_ok "Legacy installation files removed"
fi

# ── Install / update ────────────────────────────────────────────────────────
echo ""
if [[ "$EXISTING_SAME_PATH" -eq 1 ]]; then
  ui_section "Update"
else
  ui_section "Install"
fi

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
