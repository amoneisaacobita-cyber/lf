@echo off
cd /d "%~dp0"
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" server.py
  goto :done
)
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" (
  "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" server.py
  goto :done
)
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 server.py
) else (
  python server.py
)
if errorlevel 1 (
  echo The Life Fountain Church server stopped. Install Python 3.10 or newer and try again.
  pause
)
:done
