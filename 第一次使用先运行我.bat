@echo off
setlocal
cd /d "%~dp0"

rem ---------------------------------------------------------------
rem  This launcher is deliberately ASCII-only.
rem  The console code page on a fresh Windows may be 437/1252, where
rem  Chinese source text inside a .bat file would be mangled. All
rem  Chinese text is printed later by bootstrap.ps1, which switches
rem  the code page and the console font first.
rem ---------------------------------------------------------------

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap.ps1"

if errorlevel 9009 (
  echo.
  echo   PowerShell was not found on this system.
  echo   Windows 10 or later is required.
  echo.
  pause
)

endlocal
