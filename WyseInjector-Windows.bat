@echo off
setlocal
rem Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
rem Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
rem
rem This tool needs Linux disk/build tooling (apt-get, lsblk, xorriso, 7z,
rem rsync, isolinux) and writes directly to Linux block devices - it cannot
rem run on Windows itself. WSL2 with Ubuntu is the supported route (the
rem same one this project's setup.sh and README already document).

where wsl >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    echo Wyse 3040 Klipper Injector needs a Linux environment and can't run
    echo directly on Windows. WSL2 was found on this PC.
    echo.
    echo Opening a WSL2 Ubuntu shell in this folder. From there, run:
    echo     sudo apt install python3-tk
    echo     python3 wyse_injector.py
    echo.
    pause
    wsl.exe -d Ubuntu
) else (
    echo ========================================================
    echo  Wyse 3040 Klipper Injector - can't run on Windows directly
    echo ========================================================
    echo.
    echo This tool depends on Linux-only tools ^(apt-get, lsblk, xorriso, 7z,
    echo rsync, isolinux^) and writes directly to Linux block devices.
    echo.
    echo Install WSL2 with Ubuntu first, from an admin PowerShell:
    echo     wsl --install -d Ubuntu
    echo Then re-run this launcher, or inside WSL2 run:
    echo     sudo apt install python3-tk
    echo     python3 wyse_injector.py
    echo.
    pause
)
