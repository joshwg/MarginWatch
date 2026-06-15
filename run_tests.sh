#!/usr/bin/env bash
# run_tests.sh — run the MarginWatch test suite via WSL
# Usage (from Windows):  wsl -e bash run_tests.sh
#        (from WSL):     bash run_tests.sh

set -euo pipefail
cd "$(dirname "$0")"

PYTHON=venv/bin/python3
PYTHONPATH=src:../option_lib
export PYTHONPATH

echo "=== MarginWatch tests ==="
echo

passed=0
failed=0

run() {
    local label="$1"
    local script="$2"
    if "$PYTHON" "$script"; then
        passed=$((passed + 1))
    else
        echo "FAILED: $label"
        failed=$((failed + 1))
    fi
    echo
}

run "risk balls"        tests/test_risk_balls.py
run "config risk-free"  tests/test_config_risk_free.py

echo "================================"
echo "$passed suite(s) passed  $failed suite(s) failed"
[ "$failed" -eq 0 ]
