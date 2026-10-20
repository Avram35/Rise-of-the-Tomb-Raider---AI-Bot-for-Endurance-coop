@echo off
rem First run: creates bot\venv and installs the requirements. Then opens the launcher GUI.
cd /d "%~dp0bot"
if not exist "venv\Scripts\python.exe" (
    echo Creating Python environment ^(first run only^)...
    py -3 -m venv venv || python -m venv venv
    venv\Scripts\python -m pip install --upgrade pip
    venv\Scripts\python -m pip install -r ..\requirements.txt
)
start "" "venv\Scripts\pythonw.exe" launcher.py
