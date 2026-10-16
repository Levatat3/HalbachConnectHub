@echo off
rem Starts ConnectHub with no console window. It never runs at startup;
rem use "Quit ConnectHub" in the window to stop it.
cd /d "%~dp0"
start "" pyw -3 -m connecthub
