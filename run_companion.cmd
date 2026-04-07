@echo off
setlocal

set "ROOT=%~dp0"
set "PYTHON_BIN="

if exist "%ROOT%.gstrain310\Scripts\pythonw.exe" set "PYTHON_BIN=%ROOT%.gstrain310\Scripts\pythonw.exe"
if not defined PYTHON_BIN if exist "%ROOT%.gstrain311\Scripts\pythonw.exe" set "PYTHON_BIN=%ROOT%.gstrain311\Scripts\pythonw.exe"
if not defined PYTHON_BIN if exist "%ROOT%.gstrain310\Scripts\python.exe" set "PYTHON_BIN=%ROOT%.gstrain310\Scripts\python.exe"
if not defined PYTHON_BIN if exist "%ROOT%.gstrain311\Scripts\python.exe" set "PYTHON_BIN=%ROOT%.gstrain311\Scripts\python.exe"
if not defined PYTHON_BIN set "PYTHON_BIN=pythonw.exe"

start "" /D "%ROOT%" "%PYTHON_BIN%" -m companion_app %*
exit /b 0
