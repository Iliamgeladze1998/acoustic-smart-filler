@echo off
REM Starts Chrome + Smart Filler with no visible CMD window.
REM (Hands off to START.vbs immediately.)
cd /d "%~dp0"
wscript //nologo "%~dp0START.vbs"
exit /b 0
