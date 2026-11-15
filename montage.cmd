@echo off
chcp 65001 >nul
setlocal EnableExtensions
set "ROOT=%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "HF_HUB_DISABLE_SYMLINKS_WARNING=1"
set "PATH=C:\Program Files\nodejs;%PATH%"

if not exist "%ROOT%vendor\OpenMontage\.venv\Scripts\python.exe" (
  echo [ERROR] Python-okruzhenie OpenMontage ne naideno.
  echo Zapustite: python -m uv venv --python 3.12 vendor\OpenMontage\.venv
  exit /b 1
)

pushd "%ROOT%" >nul
"%ROOT%vendor\OpenMontage\.venv\Scripts\python.exe" -m engine.cli %*
set "CODE=%ERRORLEVEL%"
popd >nul
exit /b %CODE%
