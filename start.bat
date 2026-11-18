@echo off
chcp 65001 >nul
title MCQ Bot 35 - Controller
echo ========================================================
echo               🤖 MCQ Bot 35 Launcher
echo ========================================================
echo.

if not exist .env (
    echo [!] Warning: .env file not found. Copying from .env.example...
    copy .env.example .env
    echo [*] Please edit .env and set your TELEGRAM_BOT_TOKEN!
    echo.
)

python manage.py check
echo.
echo Select action:
echo  [1] Start Bot (python main.py)
echo  [2] Run Unit Tests (python manage.py test)
echo  [3] Export Quizzes to Excel CSV (python manage.py export)
echo  [4] Exit
echo.
set /p choice="Enter choice (1-4, default 1): "

if "%choice%"=="2" (
    python manage.py test
    pause
    exit /b
)
if "%choice%"=="3" (
    python manage.py export -f csv
    pause
    exit /b
)
if "%choice%"=="4" (
    exit /b
)

echo [*] Starting bot...
python main.py
pause
