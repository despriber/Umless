@echo off
rem Start Umless in the background (no console window). Quit from the tray icon.
start "" "%~dp0.venv\Scripts\pythonw.exe" -m umless
