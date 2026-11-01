@echo off
rem Builds "dist\yt-dlp GUI.exe" - a single standalone exe with yt-dlp.exe bundled inside.
rem Usage: double-click, or run "build.bat /nopause" from a script.
setlocal
cd /d "%~dp0"
set "ROOT=%~dp0"
set "VENV=%ROOT%.build-venv"

if not exist "%VENV%\Scripts\python.exe" (
    echo Creating build environment...
    py -3 -m venv "%VENV%" || goto :fail
)

echo Installing / updating PyInstaller...
"%VENV%\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check --upgrade pyinstaller || goto :fail

if not exist "%ROOT%bin\yt-dlp.exe" (
    echo Downloading yt-dlp.exe...
    if not exist "%ROOT%bin" mkdir "%ROOT%bin"
    curl.exe -L --fail -o "%ROOT%bin\yt-dlp.exe" https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe || goto :fail
)

echo Building...
"%VENV%\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --windowed ^
    --name "yt-dlp GUI" ^
    --icon "%ROOT%icon.ico" ^
    --add-data "%ROOT%bin\yt-dlp.exe;." ^
    --add-data "%ROOT%icon.ico;." ^
    --distpath "%ROOT%dist" --workpath "%ROOT%.build\work" --specpath "%ROOT%.build" ^
    "%ROOT%yt_dlp_gui.pyw" || goto :fail

echo.
echo SUCCESS: built "%ROOT%dist\yt-dlp GUI.exe"
set "RC=0"
goto :end

:fail
echo.
echo BUILD FAILED - see the messages above.
set "RC=1"

:end
if /i not "%~1"=="/nopause" pause
exit /b %RC%
