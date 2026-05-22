# MarginWatch

## Structure

```
MarginWatch/
├── src/        all Python source code
├── data/       SQLite database (outside the web root)
├── venv/       Python virtual environment (./venv, not ./.venv)
└── pack.sh     packaging script
```

## Environment
venv is installed in ./venv and not ./.venv

### Python / Running Code

All Python commands must be run via WSL, not native Windows. Use the Bash tool (not PowerShell) for any Python invocations.

```bash
# desktop app
export PYTHONPATH=src ; python src/main.py

# web server (MARGIN_PWD must be set)
export PYTHONPATH=src MARGIN_PWD=yourpassword ; python src/main_web.py

# wrong — do not use PowerShell for Python
python src/main.py   # via PowerShell tool
```

This applies to: running scripts, installing packages (`pip install`), running tests, and any other Python tooling.
