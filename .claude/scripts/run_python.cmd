@echo off
setlocal EnableExtensions EnableDelayedExpansion

if "%~1"=="" (
  echo ERROR: run_python.cmd requires a Python script path. 1>&2
  exit /b 64
)

set "SCRIPT_DIR=%~dp0"
set "ENV_FILE=%SCRIPT_DIR%..\.env"
if exist "%ENV_FILE%" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
    set "ENV_KEY=%%A"
    if /i "!ENV_KEY:~-14!"=="TASKCTL_PYTHON" set "TASKCTL_PYTHON=%%B"
  )
)

if defined TASKCTL_PYTHON (
  if exist "%TASKCTL_PYTHON%" (
    "%TASKCTL_PYTHON%" --version >nul 2>nul
    if not errorlevel 1 (
      "%TASKCTL_PYTHON%" %*
      exit /b %errorlevel%
    )
  )
)

where python >nul 2>nul
if not errorlevel 1 (
  python --version >nul 2>nul
  if not errorlevel 1 (
    python %*
    exit /b %errorlevel%
  )
)

where py >nul 2>nul
if not errorlevel 1 (
  py -3 --version >nul 2>nul
  if not errorlevel 1 (
    py -3 %*
    exit /b %errorlevel%
  )
)

echo ERROR: No runnable Python was found for cc-router-codex hooks. 1>&2
echo Install Python 3.11+ or update TASKCTL_PYTHON in .claude\.env, then rerun the command. 1>&2
exit /b 127
