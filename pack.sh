#!/usr/bin/env bash
# pack.sh — bundle the MarginWatch web server for deployment
#
# Creates ../MarginWatch.tgz containing:
#
#   MarginWatch/               web server source (no desktop files or tests)
#     requirements.txt
#     src/  (excluding ui/ ui_styles.py main.py)
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
#   MARGIN_PWD=yourpassword \
#     gunicorn --bind 0.0.0.0:5000 main_web:app
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$(basename "$SCRIPT_DIR")"
PARENT="$(dirname "$SCRIPT_DIR")"
OUT="$PARENT/$PROJECT.tgz"

rm -f "$OUT"

tar -czf "$OUT" \
    --exclude='.git' \
    --exclude='.gitignore' \
    --exclude='venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='*.bat' \
    --exclude='*/tests' \
    --exclude='MarginWatch/build' \
    --exclude='.pytest_cache' \
    --exclude='*/.pytest_cache' \
    --exclude='MarginWatch/src/ui' \
    --exclude='MarginWatch/src/ui_styles.py' \
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
