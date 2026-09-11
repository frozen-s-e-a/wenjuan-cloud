@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% equ 0 (
    py -3 scripts\start.py
) else (
    python scripts\start.py
)
if errorlevel 1 (
    echo.
    echo 启动失败，请检查上方提示。首次运行需要 Python 3.11+ 和 Node.js 22 LTS。
    pause
)
