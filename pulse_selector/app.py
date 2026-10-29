"""Application composition and startup only."""

import sys
import pyqtgraph as pg
from PySide6.QtWidgets import QApplication
from .controller import PulseController
from .branding import APP_NAME, application_icon
from . import __version__


def main() -> int:
    if sys.platform == 'win32':
        # Give the Windows taskbar this app's identity rather than Python's.
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            'IliaKuznetcov.PermeabilityStudio')
    pg.setConfigOptions(antialias=False)
    application = QApplication(sys.argv)
    application.setApplicationName(APP_NAME)
    application.setApplicationDisplayName(APP_NAME)
    application.setApplicationVersion(__version__)
    application.setWindowIcon(application_icon())
    controller = PulseController()
    controller.view.show()
    return application.exec()
