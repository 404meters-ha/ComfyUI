@echo off
chcp 65001 >nul
cd /d D:\py-workplace\ComfyUI
call .venv\Scripts\activate.bat
echo.
echo ============================================
echo  ComfyUI starting...  http://127.0.0.1:8188
echo  Press Ctrl+C in this window to stop it.
echo ============================================
echo.
python main.py
pause
