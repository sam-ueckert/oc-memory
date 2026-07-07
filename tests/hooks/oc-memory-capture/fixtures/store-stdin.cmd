@echo off
setlocal enabledelayedexpansion

REM Test fixture for oc-memory-capture handler.
REM Invoked by execFile as: store-stdin.cmd store-stdin <json-file>
REM Simulates the oc-memory CLI's store-stdin subcommand.

set SUBCOMMAND=%1
set JSON_FILE=%2

if not "%SUBCOMMAND%"=="store-stdin" goto :usage
if "%JSON_FILE%"=="" goto :usage
if not exist "%JSON_FILE%" exit /b 1

REM Read the file content
set /p CONTENTS=<"%JSON_FILE%"

REM Check for test signals
echo %CONTENTS% | findstr "__STALL__" >nul
if not errorlevel 1 (
    REM Hang for 60 seconds to trigger timeout
    ping -n 60 127.0.0.1 >nul
    exit /b 0
)

echo %CONTENTS% | findstr "__FAIL__" >nul
if not errorlevel 1 (
    exit /b 1
)

REM Normal success: echo the payload
echo capture-stored: %CONTENTS%
exit /b 0

:usage
echo usage: store-stdin.cmd store-stdin ^<file^>
exit /b 1
