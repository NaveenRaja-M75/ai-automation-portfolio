@echo off
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
python run.py queue
echo.
echo Send them from the page that just opened, 2 to 3 minutes apart.
echo Then double-click 9_Record_Sent.
pause
