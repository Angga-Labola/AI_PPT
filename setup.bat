@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
	echo uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/
	exit /b 1
)
uv sync --locked
exit /b %errorlevel%