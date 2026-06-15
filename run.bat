
rem run via the virtual environment
wsl -e bash -c "cd \"$(wslpath '%~dp0')\" && PYTHONPATH=src venv/bin/python src/main.py 2>&1"
