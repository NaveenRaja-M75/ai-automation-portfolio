@echo off
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
echo Which channel did you just send?  whatsapp  or  instagram
set /p CH=Channel: 
echo.
echo Any names you did NOT send to? Separate with commas, or just press Enter.
set /p SKIP=Skip: 
if "%SKIP%"=="" (python run.py done %CH%) else (python run.py done %CH% --skip "%SKIP%")
echo.
pause
