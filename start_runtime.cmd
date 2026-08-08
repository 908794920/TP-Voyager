@echo off
setlocal EnableExtensions DisableDelayedExpansion

rem Keep the MCP process environment small and deterministic.
set "ELECTRON_RUN_AS_NODE="
set "PYTHONHOME="
set "PYTHONPATH="
set "VIRTUAL_ENV="
set "CONDA_PREFIX="
set "CONDA_DEFAULT_ENV="

if not defined AGENT_RUNTIME_PYTHON set "AGENT_RUNTIME_PYTHON=D:\ProgramData\miniconda3\python.exe"
if not exist "%AGENT_RUNTIME_PYTHON%" (
  >&2 echo agent-runtime: Python was not found: "%AGENT_RUNTIME_PYTHON%"
  >&2 echo Set AGENT_RUNTIME_PYTHON to the absolute python.exe path.
  exit /b 9009
)

for %%I in ("%AGENT_RUNTIME_PYTHON%") do set "_RUNTIME_PYTHON_DIR=%%~dpI"
set "PATH=%_RUNTIME_PYTHON_DIR%;%_RUNTIME_PYTHON_DIR%Library\bin;%_RUNTIME_PYTHON_DIR%Scripts;%SystemRoot%\System32;%SystemRoot%"

pushd "%~dp0"
"%AGENT_RUNTIME_PYTHON%" -m agent_runtime.server
set "_RUNTIME_EXIT=%ERRORLEVEL%"
popd
exit /b %_RUNTIME_EXIT%
