@echo off
rem Sends a folder, file or text to Halbach ConnectHub's inbox for review. Works without installing anything.
rem   halbach-submit.bat <folder|file> --name "Name" --desc "One line"
rem   type notes.md | halbach-submit.bat --stdin --name "Name"
rem Nothing is published or switched on: the submission waits, switched off, until you review it in the window.
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
py -3 -m halbach_connecthub submit %*
