#!/usr/bin/env bash
# pack.sh — bundle the MarginWatch web server for deployment
#
# Creates ../MarginWatch.tgz containing:
#
#   MarginWatch/               web server source (no desktop files or tests)
#     requirements.txt
#     src/  (excluding ui/ and main.py — desktop-only files)
#   option_lib/                shared library (sibling directory)
#
# Deploy on the server:
#   tar -xzf MarginWatch.tgz
#   cd MarginWatch/src
#   pip install -r ../requirements.txt
#   pip install ../../option_lib
#   export MARGIN_PWD=yourpassword
#   python main_web.py                              # Flask dev server (port 5000)
#
#   # Production (gunicorn):
#   # --threads 2 lets /api/fetch-progress polls be served concurrently while
#   # /api/prices is blocking on market-data fetches (required for status bar).
#   MARGIN_PWD=yourpassword \
#     gunicorn --bind 0.0.0.0:5000 --threads 2 main_web:app
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$(basename "$SCRIPT_DIR")"
PARENT="$(dirname "$SCRIPT_DIR")"
OUT="$PARENT/$PROJECT.tgz"

rm -f "$OUT"

tar -czf "$OUT" \
    --mode='u=rwX,g=rwX,o=rX' \
    --exclude='.git' \
    --exclude='.gitignore' \
    --exclude='.claude' \
    --exclude='venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='*.bat' \
    --exclude='*.sh' \
    --exclude='*.md' \
    --exclude='*/tests' \
    --exclude='*/build' \
    --exclude='.pytest_cache' \
    --exclude='*/.pytest_cache' \
    --exclude='MarginWatch/src/ui' \
    --exclude='MarginWatch/src/main.py' \
    -C "$PARENT" \
    "$PROJECT" \
    option_lib

SIZE=$(du -sh "$OUT" | cut -f1)
echo "Created: $OUT  ($SIZE)"
echo ""
echo "Deploy:"
echo "  tar -xzf $PROJECT.tgz && cd $PROJECT/src"
echo "  pip install -r ../requirements.txt && pip install ../../option_lib"
echo "  export MARGIN_PWD=yourpassword"
echo "  python main_web.py"
