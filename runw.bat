
rem run via the virtual environment
wsl -e bash -c "cd \"$(wslpath '%~dp0')\" && PYTHONPATH=src MARGIN_PWD=test venv/bin/python src/main_web.py 2>&1"
