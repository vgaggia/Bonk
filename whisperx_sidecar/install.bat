@echo off
REM Create a virtual environment for the whisperx sidecar
python -m venv venv
call venv\Scripts\activate.bat

pip install --upgrade pip
pip install -r requirements.txt

echo.
echo WhisperX sidecar environment set up.
echo To start the server, run:
echo   venv\Scripts\activate.bat
echo   python server.py
pause

