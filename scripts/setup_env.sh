#!/usr/bin/env bash
# Local development environment (project .venv; does not install to /usr/local).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -d "$ROOT/.venv" ]]; then
  echo "Creating virtual environment in .venv …"
  python3 -m venv "$ROOT/.venv"
fi

# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"

python -m pip install -U pip setuptools wheel >/dev/null
pip install -e ".[dev]"

echo "Development environment ready. Activate with:"
echo "  source \"$ROOT/.venv/bin/activate\""
