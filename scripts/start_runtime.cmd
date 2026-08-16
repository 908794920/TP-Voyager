@echo off
setlocal EnableExtensions DisableDelayedExpansion

rem TP-Voyager Runtime launcher.
rem Python priority:
rem   1. TP_VOYAGER_PYTHON
rem   2. .venv\Scripts\python.exe
rem   3. python.exe from PATH

set "ELECTRON_RUN_AS_NODE="
set "PYTHONHOME="
set "PYTHONPATH="

for %%I in ("%~dp0..") do set "_TPV_ROOT=%%~fI"
set "_TPV_PYTHON="

if defined TP_VOYAGER_PYTHON (
  if exist "%TP_VOYAGER_PYTHON%" (
    set "_TPV_PYTHON=%TP_VOYAGER_PYTHON%"
  ) else (
    >&2 echo TP-Voyager: TP_VOYAGER_PYTHON does not exist: "%TP_VOYAGER_PYTHON%"
    exit /b 9009
  )
)

if not defined _TPV_PYTHON (
  if exist "%_TPV_ROOT%\.venv\Scripts\python.exe" (
    set "_TPV_PYTHON=%_TPV_ROOT%\.venv\Scripts\python.exe"
  )
)

if not defined _TPV_PYTHON (
  for %%I in (python.exe) do set "_TPV_PYTHON=%%~$PATH:I"
)

if not defined _TPV_PYTHON (
  >&2 echo TP-Voyager: Python was not found.
  >&2 echo Create .venv or set TP_VOYAGER_PYTHON to an absolute python.exe path.
  exit /b 9009
)

pushd "%_TPV_ROOT%"
"%_TPV_PYTHON%" -m agent_runtime.server
set "_RUNTIME_EXIT=%ERRORLEVEL%"
popd
exit /b %_RUNTIME_EXIT%
