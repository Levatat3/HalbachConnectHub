@echo off
rem Starts Halbach ConnectHub with no console window. It never runs at startup;
rem use "Quit" in the window to stop it.
cd /d "%~dp0"
start "" pyw -3 -m halbach_connecthub
