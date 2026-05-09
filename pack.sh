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
    -C "$(dirname "$SCRIPT_DIR")" \
    "$(basename "$SCRIPT_DIR")"

echo "Created: $OUT"
