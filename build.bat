@echo off
rem Builds yt-dlp GUI with PyInstaller.
rem
rem   build.bat            Folder build (recommended): dist\yt-dlp-GUI\ and dist\yt-dlp-GUI-win64.zip.
rem                        Nothing is unpacked at startup, so antivirus/sandbox reports stay quiet.
rem   build.bat /onefile   Single self-extracting dist\yt-dlp-GUI.exe with yt-dlp bundled inside.
rem
rem Add /nopause to skip the final pause when running from a script.
setlocal
cd /d "%~dp0"
set "ROOT=%~dp0"
set "VENV=%ROOT%.build-venv"
set "ONEFILE="
set "NOPAUSE="
for %%A in (%*) do (
    if /i "%%~A"=="/onefile" set "ONEFILE=1"
    if /i "%%~A"=="/nopause" set "NOPAUSE=1"
)

if not exist "%VENV%\Scripts\python.exe" (
    echo Creating build environment...
    py -3 -m venv "%VENV%" || goto :fail
)

echo Installing / updating PyInstaller...
"%VENV%\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r "%ROOT%requirements-build.txt" || goto :fail

if not exist "%ROOT%bin\yt-dlp.exe" (
    echo Downloading yt-dlp.exe...
    if not exist "%ROOT%bin" mkdir "%ROOT%bin"
    curl.exe -L --fail -o "%ROOT%bin\yt-dlp.exe" https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe || goto :fail
)

if defined ONEFILE goto :onefile

echo Building folder version...
"%VENV%\Scripts\python.exe" -m PyInstaller --noconfirm --onedir --windowed ^
    --name "yt-dlp-GUI" ^
    --icon "%ROOT%icon.ico" ^
    --add-data "%ROOT%icon.ico;." ^
    --distpath "%ROOT%dist" --workpath "%ROOT%.build\work-onedir" --specpath "%ROOT%.build" ^
    "%ROOT%yt_dlp_gui.pyw" || goto :fail

rem yt-dlp ships ready to use in bin\, where the app looks first and can update it in place
mkdir "%ROOT%dist\yt-dlp-GUI\bin" 2>nul
copy /y "%ROOT%bin\yt-dlp.exe" "%ROOT%dist\yt-dlp-GUI\bin\" >nul || goto :fail

echo Zipping...
if exist "%ROOT%dist\yt-dlp-GUI-win64.zip" del "%ROOT%dist\yt-dlp-GUI-win64.zip"
"%VENV%\Scripts\python.exe" -c "import shutil; shutil.make_archive(r'%ROOT%dist\yt-dlp-GUI-win64', 'zip', r'%ROOT%dist', 'yt-dlp-GUI')" || goto :fail

echo.
echo SUCCESS: built "%ROOT%dist\yt-dlp-GUI\yt-dlp-GUI.exe"
echo          and   "%ROOT%dist\yt-dlp-GUI-win64.zip"
set "RC=0"
goto :end

:onefile
echo Building single-file version...
"%VENV%\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --windowed ^
    --name "yt-dlp-GUI" ^
    --icon "%ROOT%icon.ico" ^
    --add-data "%ROOT%bin\yt-dlp.exe;." ^
    --add-data "%ROOT%icon.ico;." ^
    --distpath "%ROOT%dist" --workpath "%ROOT%.build\work-onefile" --specpath "%ROOT%.build" ^
    "%ROOT%yt_dlp_gui.pyw" || goto :fail

echo.
echo SUCCESS: built "%ROOT%dist\yt-dlp-GUI.exe"
set "RC=0"
goto :end

:fail
echo.
echo BUILD FAILED - see the messages above.
set "RC=1"

:end
if not defined NOPAUSE pause
exit /b %RC%
