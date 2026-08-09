@echo off
setlocal
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"

if not exist "%CHROME%" (
  echo Google Chrome was not found.
  echo Install Chrome and try again.
  pause
  exit /b 1
)

set "SMART_PROFILE=%LocalAppData%\AcousticSmartFiller\ChromeProfile"
start "Acoustic Smart Chrome" "%CHROME%" --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 --user-data-dir="%SMART_PROFILE%" "https://acoustic.ge/aco_st_admin.php?dispatch=products.update&product_id=15650"
endlocal
