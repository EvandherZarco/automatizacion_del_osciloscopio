"""
main.py
Punto de entrada de la aplicación.
"""

import sys
import logging

from PySide6.QtWidgets import QApplication

from app import config_usuario
from app.gui.bienvenida import BienvenidaWindow
from app.gui.theme import APP_STYLESHEET

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)

    config_usuario.aplicar()

    window = BienvenidaWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()