@echo off
if /i "%~1"=="--run" goto run
"%ComSpec%" /d /k ""%~f0" --run"
exit /b

:run
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
title Wenjuan Cloud
pushd "%~dp0"
if errorlevel 1 goto path_error

echo Wenjuan Cloud - local server
if not exist "scripts\start.py" goto missing_files
echo Checking Python 3.11 or newer...
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 goto launch_py
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 goto launch_python
echo ERROR: Python 3.11 or newer was not found.
echo Install Python, enable Add Python to PATH, and run this file again.
set "WQ_EXIT=1"
goto finished

:launch_py
py -3 -u "scripts\start.py"
set "WQ_EXIT=%errorlevel%"
goto finished

:launch_python
python -u "scripts\start.py"
set "WQ_EXIT=%errorlevel%"
goto finished

:missing_files
echo ERROR: scripts\start.py was not found.
echo Extract the complete project ZIP before running start.cmd.
set "WQ_EXIT=1"
goto finished

:finished
echo.
echo Server or setup process exited with code %WQ_EXIT%.
if not "%WQ_EXIT%"=="0" echo Check the error above and startup-error.log if present.
echo This window stays open. Type exit to close it.
popd
exit /b %WQ_EXIT%

:path_error
echo ERROR: Cannot open the project folder. Extract it to a writable local folder.
exit /b 1
