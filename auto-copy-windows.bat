@echo off
setlocal
cd /d %~dp0

set "PLUGIN_PATH=%localappdata%\GOG.com\Galaxy\plugins\installed\steam_ca27391f-2675-49b1-92c0-896d43afa4f8"

rmdir /S /Q -rf "%PLUGIN_PATH%"
mkdir "%PLUGIN_PATH%"
powershell Expand-Archive ".\windows.zip" -DestinationPath %PLUGIN_PATH%