# MarginWatch

## Environment
venv is installed in ./venv and not ./.venv

### Python / Running Code

All Python commands must be run via WSL, not native Windows. Use the Bash tool (not PowerShell) for any Python invocations.

```bash
# correct
export PYTHONPATH=. ; python ui/app.py

# wrong — do not use PowerShell for Python
python ui/app.py   # via PowerShell tool
```

This applies to: running scripts, installing packages (`pip install`), running tests, and any other Python tooling.
