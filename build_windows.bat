@echo off
setlocal
cd /d "%~dp0"
echo Building Permeability Studio v1.0.0 for Windows...
if not exist ".venv-build\Scripts\python.exe" (
    where py >nul 2>nul
    if errorlevel 1 (
        python -m venv .venv-build
    ) else (
        py -3 -m venv .venv-build
    )
    if errorlevel 1 goto failed
)
".venv-build\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-build.txt
if errorlevel 1 goto failed
".venv-build\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name permeability_studio --icon "pulse_selector\assets\permeability_studio.ico" --add-data "pulse_selector\assets;pulse_selector\assets" permeability_studio.py
if errorlevel 1 goto failed
echo.
echo Built: dist\permeability_studio.exe
pause
exit /b 0
:failed
echo.
echo Build failed. Review the errors above.
pause
exit /b 1
