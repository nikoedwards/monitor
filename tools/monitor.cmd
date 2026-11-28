@echo off
setlocal
set "PYTHONUTF8=1"
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
    "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "%~dp0monitor.pyz" %*
    goto :done
)
py -3 -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>&1
if not errorlevel 1 (
    py -3 "%~dp0monitor.pyz" %*
    goto :done
)
python -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>&1
if not errorlevel 1 (
    python "%~dp0monitor.pyz" %*
    goto :done
)
echo Install Python 3.9 or newer from https://www.python.org/downloads/ and run this command again. 1>&2
exit /b 1
:done
exit /b %errorlevel%
