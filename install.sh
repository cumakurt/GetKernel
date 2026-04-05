#!/usr/bin/env bash
# Install GetKernel into .venv and add getkernel to PATH (survives reboot).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

MARKER_BEGIN="# >>> GetKernel PATH (added by install.sh)"
MARKER_END="# <<< GetKernel PATH"

usage() {
  echo "Usage: ${0##*/} [--dev] [--recreate-venv] [--no-symlink]"
  echo "  --dev           Install optional dev dependencies (pytest)"
  echo "  --recreate-venv Remove existing .venv and create a fresh one"
  echo "  --no-symlink    Do not add getkernel to PATH (venv only)"
  echo ""
  echo "Requires root (sudo). Run:  sudo ${0##*/}"
  echo "Override (not recommended): GETKERNEL_ALLOW_USER_INSTALL=1"
  exit "${1:-0}"
}

DEV=0
RECREATE=0
NO_SYMLINK=0
for arg in "$@"; do
  case "$arg" in
    --dev) DEV=1 ;;
    --recreate-venv) RECREATE=1 ;;
    --no-symlink) NO_SYMLINK=1 ;;
    -h | --help) usage 0 ;;
    *)
      echo "Unknown option: $arg" >&2
      usage 1
      ;;
  esac
done

# Require administrator privileges (sudo/root)
if [[ "${GETKERNEL_ALLOW_USER_INSTALL:-}" != "1" ]]; then
  if [[ $(id -u) -ne 0 ]]; then
    if command -v sudo >/dev/null 2>&1; then
      echo "This installer requires sudo. Requesting elevated privileges …" >&2
      exec sudo -E "$ROOT/install.sh" "$@"
    fi
    echo "Error: install.sh must be run as root. Use: sudo \"$ROOT/install.sh\" $*" >&2
    exit 1
  fi
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is required but not found in PATH." >&2
  exit 1
fi

python3 - <<'PY' || exit 1
import sys
if sys.version_info < (3, 8):
    sys.stderr.write("Error: Python 3.8 or newer is required.\n")
    sys.exit(1)
PY

if [[ "$RECREATE" -eq 1 ]] && [[ -d "$ROOT/.venv" ]]; then
  echo "Removing existing .venv …"
  rm -rf "$ROOT/.venv"
fi

if [[ ! -d "$ROOT/.venv" ]]; then
  echo "Creating virtual environment in .venv …"
  python3 -m venv "$ROOT/.venv"
fi

# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"

echo "Upgrading pip …"
python -m pip install -U pip setuptools wheel >/dev/null

if [[ "$DEV" -eq 1 ]]; then
  echo "Installing GetKernel in editable mode with dev extras …"
  pip install -e ".[dev]"
else
  echo "Installing GetKernel in editable mode …"
  pip install -e .
fi

# venv was created as root; hand it back to the invoking user when using sudo
if [[ -n "${SUDO_USER:-}" ]]; then
  ug=$(id -g "$SUDO_USER" 2>/dev/null || echo "$SUDO_USER")
  echo "Setting ownership of .venv to $SUDO_USER …"
  chown -R "$SUDO_USER:$ug" "$ROOT/.venv"
  find "$ROOT" -maxdepth 1 \( -name '*.egg-info' -o -name '*.dist-info' \) \
    -exec chown -R "$SUDO_USER:$ug" {} + 2>/dev/null || true
fi

VENV_GETKERNEL="$ROOT/.venv/bin/getkernel"
if [[ -f "$VENV_GETKERNEL" ]] && [[ ! -x "$VENV_GETKERNEL" ]]; then
  chmod +x "$VENV_GETKERNEL" || true
fi

# Append ~/.local/bin PATH block if missing (idempotent)
_append_path_snippet() {
  local home_dir="$1"
  local owner="${2:-}"

  _patch_file() {
    local f="$1"
    if [[ "$f" == "$home_dir/.profile" ]] && [[ ! -f "$f" ]]; then
      touch "$f"
      if [[ -n "$owner" ]]; then
        chown "$owner:" "$f" 2>/dev/null || true
      fi
    fi
    [[ -f "$f" ]] || return 0
    if grep -qF "$MARKER_BEGIN" "$f" 2>/dev/null; then
      return 0
    fi
    {
      echo ""
      echo "$MARKER_BEGIN"
      echo 'if [[ -d "$HOME/.local/bin" ]]; then'
      echo '  case ":${PATH}:" in *":$HOME/.local/bin:"*) ;; *) PATH="$HOME/.local/bin:$PATH" ;; esac'
      echo "fi"
      echo "$MARKER_END"
    } >> "$f"
    if [[ -n "$owner" ]]; then
      chown "$owner:" "$f" 2>/dev/null || true
    fi
    echo "Updated PATH in: $f"
  }

  _patch_file "$home_dir/.profile"
  [[ -f "$home_dir/.zshrc" ]] && _patch_file "$home_dir/.zshrc"
  [[ -f "$home_dir/.bashrc" ]] && _patch_file "$home_dir/.bashrc"
}

install_symlink_and_path() {
  local euid
  euid=$(id -u)

  # sudo ./install.sh → configure real user's ~/.local/bin, not root
  if [[ "$euid" -eq 0 ]] && [[ -n "${SUDO_USER:-}" ]]; then
    local uh
    uh=$(getent passwd "$SUDO_USER" | cut -d: -f6)
    if [[ -n "$uh" ]] && [[ -d "$uh" ]]; then
      mkdir -p "$uh/.local/bin"
      ln -sf "$VENV_GETKERNEL" "$uh/.local/bin/getkernel"
      chown -h "$SUDO_USER:$(id -g "$SUDO_USER" 2>/dev/null || echo "$SUDO_USER")" \
        "$uh/.local/bin/getkernel" 2>/dev/null || true
      echo "Symlink for user $SUDO_USER: $uh/.local/bin/getkernel"
      _append_path_snippet "$uh" "$SUDO_USER"
      # Root shells do not see ~/.local/bin of SUDO_USER; provide a system-wide entry too.
      ln -sf "$VENV_GETKERNEL" "/usr/local/bin/getkernel"
      echo "Symlink (system): /usr/local/bin/getkernel  (works in root shells and for all users)"
      return 0
    fi
  fi

  if [[ "$euid" -eq 0 ]]; then
    ln -sf "$VENV_GETKERNEL" "/usr/local/bin/getkernel"
    echo "Symlink (system): /usr/local/bin/getkernel"
    return 0
  fi

  mkdir -p "$HOME/.local/bin"
  ln -sf "$VENV_GETKERNEL" "$HOME/.local/bin/getkernel"
  echo "Symlink (user): $HOME/.local/bin/getkernel"
  _append_path_snippet "$HOME" ""
}

echo ""
if [[ "$NO_SYMLINK" -eq 1 ]]; then
  echo "Skipping PATH symlink (--no-symlink). Use:"
  echo "  source \"$ROOT/.venv/bin/activate\""
  echo "  getkernel --help"
else
  echo "Adding getkernel to PATH (works in new shells after reboot) …"
  if ! install_symlink_and_path; then
    echo "Warning: Symlink step had an issue. Fallback:" >&2
    echo "  \"$ROOT/.venv/bin/getkernel\" --help" >&2
  fi
fi

echo ""
echo "Done."
echo ""
if [[ "$NO_SYMLINK" -eq 0 ]]; then
  export PATH="${HOME}/.local/bin:/usr/local/bin:${PATH}"
  if command -v getkernel >/dev/null 2>&1; then
    echo "Try:  getkernel --help"
    echo "(Current shell: run  source ~/.profile  or open a new terminal if needed.)"
  else
    echo "Run:  getkernel --help"
    echo "If not found yet:  source ~/.profile   or log out and back in."
    echo "Direct:  \"$ROOT/.venv/bin/getkernel\" --help"
  fi
else
  echo "Activate:  source \"$ROOT/.venv/bin/activate\""
  echo "Then:      getkernel --help"
fi
echo ""
