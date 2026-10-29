"""Application identity and bundled desktop icon."""
from pathlib import Path
from PySide6.QtGui import QIcon

APP_NAME = 'Permeability Studio'
ASSET_DIRECTORY = Path(__file__).resolve().parent / 'assets'


def application_icon():
    return QIcon(str(ASSET_DIRECTORY / 'permeability_studio.ico'))
