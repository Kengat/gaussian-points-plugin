@echo off
setlocal
powershell -ExecutionPolicy Bypass -File "%~dp0build_native.ps1" %*
exit /b %ERRORLEVEL%
