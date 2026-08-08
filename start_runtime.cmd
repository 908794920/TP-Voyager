@echo off
setlocal EnableExtensions DisableDelayedExpansion

rem TP-Voyager Runtime launcher.
rem Python priority:
rem   1. AGENT_RUNTIME_PYTHON
rem   2. .venv\Scripts\python.exe
rem   3. python.exe from PATH

set "ELECTRON_RUN_AS_NODE="
set "PYTHONHOME="
set "PYTHONPATH="

set "_TPV_PYTHON="

if defined AGENT_RUNTIME_PYTHON (
  if exist "%AGENT_RUNTIME_PYTHON%" (
    set "_TPV_PYTHON=%AGENT_RUNTIME_PYTHON%"
  ) else (
    >&2 echo TP-Voyager: AGENT_RUNTIME_PYTHON does not exist: "%AGENT_RUNTIME_PYTHON%"
    exit /b 9009
  )
)

if not defined _TPV_PYTHON (
  if exist "%~dp0.venv\Scripts\python.exe" (
    set "_TPV_PYTHON=%~dp0.venv\Scripts\python.exe"
  )
)

if not defined _TPV_PYTHON (
  for %%I in (python.exe) do set "_TPV_PYTHON=%%~$PATH:I"
)

if not defined _TPV_PYTHON (
  >&2 echo TP-Voyager: Python was not found.
  >&2 echo Create .venv or set AGENT_RUNTIME_PYTHON to an absolute python.exe path.
  exit /b 9009
)

pushd "%~dp0"
"%_TPV_PYTHON%" -m agent_runtime.server
set "_RUNTIME_EXIT=%ERRORLEVEL%"
popd
exit /b %_RUNTIME_EXIT%
