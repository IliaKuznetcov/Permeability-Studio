# Windows application and packaging

## Run from source

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python permeability_studio.py
```

`pulse_log_selector.py` is an alternate launcher that imports the same `pulse_selector.app.main` function.

## Create a single executable

On Windows, double-click `build_windows.bat`. This creates an isolated `.venv-build`, installs dependencies and PyInstaller, and builds:

```text
dist\permeability_studio.exe
```

The exe embeds `pulse_selector/assets/permeability_studio.ico`; the assets directory is bundled as application data. It may launch more slowly than a one-folder build because its dependencies are extracted at startup. Python is not required on the target Windows computer, but the generated binary must still be tested on intended systems.

For a direct build in an already prepared virtual environment:

```powershell
python -m pip install -r requirements.txt -r requirements-build.txt
python -m PyInstaller --noconfirm --clean --onefile --windowed --name permeability_studio --icon "pulse_selector\assets\permeability_studio.ico" --add-data "pulse_selector\assets;pulse_selector\assets" permeability_studio.py
```

The build must be performed on Windows to create a Windows executable. The original images are in `pulse_selector/assets/`.
