#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$SCRIPT_DIR/../MarginWatch.tgz"

rm -f "$OUT"

tar -czf "$OUT" \
    --exclude='.git' \
    --exclude='venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='*/tests' \
    --exclude='.pytest_cache' \
    --exclude='*/.pytest_cache' \
    --exclude='MarginWatch/src/ui' \
    --exclude='MarginWatch/src/main.py' \
    -C "$(dirname "$SCRIPT_DIR")" \
    "$(basename "$SCRIPT_DIR")" \
    option_lib

echo "Created: $OUT"
