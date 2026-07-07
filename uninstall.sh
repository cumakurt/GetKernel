#!/usr/bin/env bash
# Remove GetKernel from /usr/local/getkernel and related symlinks.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ROOT/.venv/bin/python"
GK="$ROOT/GetKernel.py"

_R='' _B='' _D='' _CY='' _GR='' _YL='' _RD=''
if [[ -t 1 ]] && [[ -z "${NO_COLOR:-}" ]]; then
  _R=$'\033[0m'
  _B=$'\033[1m'
  _D=$'\033[2m'
  _CY=$'\033[36m'
  _GR=$'\033[32m'
  _YL=$'\033[33m'
  _RD=$'\033[31m'
fi

usage() {
  echo ""
  echo "${_CY}${_B}  GetKernel${_R} ${_D}uninstaller${_R}"
  echo ""
  echo "  Usage: ${0##*/} [--yes]"
  echo ""
  exit "${1:-0}"
}

ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --yes | -y) ASSUME_YES=1 ;;
    -h | --help) usage 0 ;;
    *)
      echo "${_RD}Unknown option: $arg${_R}" >&2
      usage 1
      ;;
  esac
done

if [[ $(id -u) -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    echo "${_YL}!${_R} Administrator privileges required — switching to sudo …"
    exec sudo "$ROOT/uninstall.sh" "$@"
  fi
  echo "${_RD}Run as root: sudo \"$ROOT/uninstall.sh\"${_R}" >&2
  exit 1
fi

ARGS=()
[[ "$ASSUME_YES" -eq 1 ]] && ARGS+=(--yes)

if [[ -x "$PY" ]]; then
  exec "$PY" "$GK" uninstall "${ARGS[@]}"
fi
exec python3 "$GK" uninstall "${ARGS[@]}"
