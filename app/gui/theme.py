"""
theme.py
Constantes visuales y helpers de UI compartidos entre todas las ventanas.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QPushButton

# ── Colores de LED ─────────────────────────────────────────────────────────────
LED_VERDE    = "#4caf50"
LED_AMARILLO = "#ffc107"
LED_ROJO     = "#f44336"
LED_GRIS     = "#555555"

_ESTILO_LED = (
    "border-radius: 7px; min-width: 14px; max-width: 14px;"
    "min-height: 14px; max-height: 14px;"
)

# ── Colores de acento ──────────────────────────────────────────────────────────
AZUL     = "#00bfff"
VERDE    = "#4caf50"
ROJO     = "#f44336"
AMARILLO = "#ffc107"
AMBER    = "#ff9800"

# ── Estilo global de la aplicación ────────────────────────────────────────────
APP_STYLESHEET = """
QMainWindow, QDialog {
    background-color: #1a1a1a;
}

QWidget {
    background-color: #1a1a1a;
    color: #e0e0e0;
    font-family: "Segoe UI", system-ui, sans-serif;
    font-size: 13px;
}

QGroupBox {
    border: 1px solid #333;
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 8px;
    font-size: 12px;
    color: #aaa;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #888;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

QFrame[frameShape="4"],
QFrame[frameShape="5"] {
    color: #333;
}

QPushButton {
    background-color: #2a2a2a;
    color: #e0e0e0;
    border: 1px solid #444;
    border-radius: 6px;
    padding: 5px 12px;
    min-height: 28px;
}

QPushButton:hover {
    background-color: #333;
    border-color: #666;
}

QPushButton:pressed {
    background-color: #1f1f1f;
}

QPushButton:disabled {
    color: #555;
    border-color: #333;
    background-color: #222;
}

QPushButton[activo="true"] {
    background-color: #003a50;
    border-color: #00bfff;
    color: #00bfff;
}

QPushButton[activo_verde="true"] {
    background-color: #1a3a1a;
    border-color: #4caf50;
    color: #4caf50;
}

QPushButton[activo_amber="true"] {
    background-color: #3a2800;
    border-color: #ff9800;
    color: #ff9800;
}

QPushButton[peligro="true"] {
    background-color: #2a1a1a;
    border-color: #f44336;
    color: #f44336;
    font-weight: bold;
}

QComboBox {
    background-color: #2a2a2a;
    border: 1px solid #444;
    border-radius: 6px;
    padding: 4px 10px;
    min-height: 28px;
    color: #e0e0e0;
}

QComboBox::drop-down {
    border: none;
    width: 24px;
}

QComboBox QAbstractItemView {
    background-color: #2a2a2a;
    border: 1px solid #444;
    selection-background-color: #003a50;
    color: #e0e0e0;
}

QSpinBox, QDoubleSpinBox {
    background-color: #2a2a2a;
    border: 1px solid #444;
    border-radius: 6px;
    padding: 4px 8px;
    min-height: 28px;
    color: #e0e0e0;
}

QSpinBox:disabled, QDoubleSpinBox:disabled {
    color: #555;
    border-color: #333;
}

QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    background-color: #333;
    border: none;
    width: 18px;
}

QTabWidget::pane {
    border: 1px solid #333;
    border-radius: 6px;
    background-color: #1e1e1e;
}

QTabBar::tab {
    background-color: #222;
    color: #888;
    border: none;
    padding: 8px 20px;
    margin-right: 2px;
    border-radius: 4px;
}

QTabBar::tab:selected {
    background-color: #1e1e1e;
    color: #00bfff;
    border-bottom: 2px solid #00bfff;
}

QTabBar::tab:hover:!selected {
    background-color: #2a2a2a;
    color: #ccc;
}

QLabel {
    background: transparent;
}

QTableWidget {
    background-color: #1e1e1e;
    border: 1px solid #333;
    gridline-color: #2a2a2a;
    color: #e0e0e0;
    selection-background-color: #003a50;
}

QTableWidget::item:selected {
    background-color: #003a50;
    color: #e0e0e0;
}

QHeaderView::section {
    background-color: #252525;
    color: #aaa;
    border: none;
    border-right: 1px solid #333;
    padding: 4px 8px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

QScrollBar:vertical {
    background: #1e1e1e;
    width: 8px;
    border-radius: 4px;
}

QScrollBar::handle:vertical {
    background: #444;
    border-radius: 4px;
    min-height: 20px;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QMessageBox {
    background-color: #1e1e1e;
}
"""

# ── Helpers ────────────────────────────────────────────────────────────────────

def make_led(color: str = LED_GRIS) -> QLabel:
    """Crea un QLabel circular que funciona como LED de estado."""
    lbl = QLabel()
    lbl.setFixedSize(14, 14)
    lbl.setStyleSheet(f"background: {color}; {_ESTILO_LED}")
    return lbl


def set_led(lbl: QLabel, color: str) -> None:
    """Actualiza el color de un LED sin recrear el widget."""
    lbl.setStyleSheet(f"background: {color}; {_ESTILO_LED}")


def set_btn_activo(btn: QPushButton, activo: bool, estilo: str = "azul") -> None:
    """
    Aplica/quita el estilo de botón 'radio activo'.
    estilo: 'azul' | 'verde' | 'amber'
    """
    for prop in ("activo", "activo_verde", "activo_amber"):
        btn.setProperty(prop, False)

    if activo:
        prop_map = {"azul": "activo", "verde": "activo_verde", "amber": "activo_amber"}
        btn.setProperty(prop_map.get(estilo, "activo"), True)

    btn.style().unpolish(btn)
    btn.style().polish(btn)


def chip_log(texto: str = "") -> QLabel:
    """Crea un chip de texto para la barra inferior de log."""
    lbl = QLabel(texto)
    lbl.setStyleSheet(
        "background: #252525; color: #aaa; font-size: 11px;"
        "border: 1px solid #333; border-radius: 4px; padding: 3px 10px;"
    )
    return lbl