#!/usr/bin/env bash
# Development environment (same as install.sh)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
exec "$ROOT/scripts/install.sh"
