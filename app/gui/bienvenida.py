"""
bienvenida.py
Ventana de bienvenida del sistema de medición fotoacústica.
Muestra los escudos institucionales y permite seleccionar el modo de operación.
Se abre al arrancar la app; cada ventana hija la oculta al abrirse y la
restaura al cerrarse mediante la señal volver.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
)

from app.gui.theme import APP_STYLESHEET

_ASSETS_DIR = Path(__file__).resolve().parent.parent.parent / "assets"

_BTN_MODO_BASE = """
    QPushButton {{
        background-color: #232323;
        color: #e0e0e0;
        border: 1px solid #3a3a3a;
        border-radius: 12px;
        font-size: 14px;
        font-weight: bold;
        min-width: 190px;
        min-height: 90px;
        padding: 10px 18px;
        text-align: center;
    }}
    QPushButton:hover {{
        background-color: #2c2c2c;
        border-color: {color};
        color: {color};
    }}
    QPushButton:pressed {{
        background-color: #1a1a1a;
    }}
"""

_BTN_LASER  = _BTN_MODO_BASE.format(color="#ff9800")
_BTN_OSCIL  = _BTN_MODO_BASE.format(color="#00bfff")
_BTN_AMBOS  = """
    QPushButton {
        background-color: #002a3a;
        color: #00bfff;
        border: 1px solid #00bfff;
        border-radius: 12px;
        font-size: 14px;
        font-weight: bold;
        min-width: 220px;
        min-height: 90px;
        padding: 10px 18px;
    }
    QPushButton:hover {
        background-color: #003a50;
        border-color: #33cfff;
        color: #33cfff;
    }
    QPushButton:pressed {
        background-color: #001f2e;
    }
"""


class BienvenidaWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sistema de Medición Fotoacústica")
        self.setMinimumSize(720, 520)
        self.setStyleSheet(APP_STYLESHEET)

        self._ventana_hija = None

        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setAlignment(Qt.AlignCenter)
        root.setContentsMargins(48, 40, 48, 40)
        root.setSpacing(0)

        root.addStretch(1)
        root.addLayout(self._fila_escudos())
        root.addSpacing(28)
        root.addWidget(self._bloque_titulo())
        root.addSpacing(36)
        root.addLayout(self._fila_botones())
        root.addStretch(2)
        root.addWidget(self._pie())

    # ── Escudos ───────────────────────────────────────────────────────────────

    def _fila_escudos(self) -> QHBoxLayout:
        fila = QHBoxLayout()
        fila.setAlignment(Qt.AlignCenter)
        fila.setSpacing(48)
        fila.addWidget(self._escudo("unam_logo.png", "UNAM"))
        fila.addWidget(self._escudo("icat_logo.png", "ICAT"))
        return fila

    def _escudo(self, nombre: str, fallback: str) -> QLabel:
        lbl = QLabel()
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setFixedSize(100, 100)
        ruta = _ASSETS_DIR / nombre
        if ruta.exists():
            pix = QPixmap(str(ruta)).scaled(
                100, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            lbl.setPixmap(pix)
        else:
            lbl.setText(fallback)
            lbl.setStyleSheet(
                "color: #999; font-size: 14px; font-weight: bold;"
                "border: 1px solid #3a3a3a; border-radius: 50px;"
                "background: #232323;"
            )
        return lbl

    # ── Título ────────────────────────────────────────────────────────────────

    def _bloque_titulo(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setAlignment(Qt.AlignCenter)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        titulo = QLabel("Sistema de Medición Fotoacústica")
        titulo.setAlignment(Qt.AlignCenter)
        titulo.setStyleSheet(
            "color: #ffffff; font-size: 22px; font-weight: bold; background: transparent;"
        )

        sub = QLabel(
            "Universidad Nacional Autónoma de México  ·  ICAT\n"
            "Asesor: Dr. Arturo Ronquillo Arvizu"
        )
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet("color: #666; font-size: 12px; background: transparent;")

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #2e2e2e; background: #2e2e2e; max-height: 1px;")

        lay.addWidget(titulo)
        lay.addWidget(sub)
        lay.addSpacing(16)
        lay.addWidget(sep)
        return w

    # ── Botones de modo ───────────────────────────────────────────────────────

    def _fila_botones(self) -> QHBoxLayout:
        fila = QHBoxLayout()
        fila.setAlignment(Qt.AlignCenter)
        fila.setSpacing(18)

        self._btn_laser = QPushButton("⚡\nLáser")
        self._btn_laser.setStyleSheet(_BTN_LASER)
        self._btn_laser.setCursor(Qt.PointingHandCursor)
        self._btn_laser.setToolTip("Control exclusivo del láser EKSPLA NL303HT-10-SH")

        self._btn_oscil = QPushButton("〰\nOsciloscopio")
        self._btn_oscil.setStyleSheet(_BTN_OSCIL)
        self._btn_oscil.setCursor(Qt.PointingHandCursor)
        self._btn_oscil.setToolTip("Control exclusivo del osciloscopio Tektronix TDS5052B")

        self._btn_ambos = QPushButton("⚡ 〰\nLáser  +  Osciloscopio")
        self._btn_ambos.setStyleSheet(_BTN_AMBOS)
        self._btn_ambos.setCursor(Qt.PointingHandCursor)
        self._btn_ambos.setToolTip(
            "Sistema completo: parámetros, medición manual y secuencia automática"
        )

        self._btn_laser.clicked.connect(self._abrir_laser)
        self._btn_oscil.clicked.connect(self._abrir_osciloscopio)
        self._btn_ambos.clicked.connect(self._abrir_ambos)

        fila.addWidget(self._btn_laser)
        fila.addWidget(self._btn_oscil)
        fila.addWidget(self._btn_ambos)
        return fila

    # ── Pie ───────────────────────────────────────────────────────────────────

    def _pie(self) -> QLabel:
        lbl = QLabel("Automatización de un sistema fotoacústico para la caracterización de líquidos")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("color: #3a3a3a; font-size: 11px; background: transparent;")
        return lbl

    # ── Apertura de ventanas hijas ────────────────────────────────────────────

    @Slot()
    def _abrir_laser(self):
        from app.gui.ventana_laser import VentanaLaser
        self._abrir_hija(VentanaLaser())

    @Slot()
    def _abrir_osciloscopio(self):
        from app.gui.ventana_osciloscopio import VentanaOsciloscopio
        self._abrir_hija(VentanaOsciloscopio())

    @Slot()
    def _abrir_ambos(self):
        from app.gui.ventana_ambos import VentanaAmbos
        self._abrir_hija(VentanaAmbos())

    def _abrir_hija(self, ventana: QMainWindow):
        ventana.volver.connect(self._on_volver)
        self._ventana_hija = ventana
        self.hide()
        ventana.show()

    @Slot()
    def _on_volver(self):
        self._ventana_hija = None
        self.show()