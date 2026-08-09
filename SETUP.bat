@echo off
REM Redirect only — opens the visual Setup (no installer UI in CMD).
cd /d "%~dp0"
wscript //nologo "%~dp0SETUP.vbs"
exit /b 0
