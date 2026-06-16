
rem run via the virtual environment
wsl -e bash -c "cd \"$(wslpath '%~dp0')\" && PYTHONPATH=src MARGIN_PWD=test MASSIVE_API_KEY='%MASSIVE_API_KEY%' venv/bin/python src/main_web.py 2>&1"
