@echo off
chcp 65001 > nul
cd /d "%~dp0"
powershell -NoProfile -Command "python main.py %*"
