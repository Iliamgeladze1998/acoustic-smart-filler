@echo off
REM Launch app only, no CMD window (uses RUN_APP.vbs + pythonw).
cd /d "%~dp0"
wscript //nologo "%~dp0RUN_APP.vbs"
exit /b 0
