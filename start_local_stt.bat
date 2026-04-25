@echo off
call "C:\Users\vgaggia\Desktop\Bonk\whisperx_sidecar\venv\Scripts\activate.bat"
cd whisperx_sidecar
set WHISPERX_MODEL=large-v3
set WHISPERX_HTTP_PORT=5001
python server.py
