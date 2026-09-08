r"""
probar_advertencias_gui.py
Prueba dirigida del manejo de advertencias en ventana_ambos, sin hardware.

Abre la ventana real, crea una sesión temporal, emite por la fachada de
Medicion las mismas advertencias que TriggerWorker produce en un barrido, y
verifica que la GUI las acumule, las cuente y las escriba en disco.

Uso:
    python herramientas\probar_advertencias_gui.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

from app.gui.ventana_ambos import VentanaAmbos

MENSAJES = [
    "La muestra está a 58.42 °C. Se omiten los objetivos por encima de esa "
    "temperatura: 60.0 °C.",
    "Lectura de temperatura interrumpida. La secuencia continúa en espera.",
    "Lectura de temperatura restablecida.",
    "El objetivo 45.0 °C se perdió durante la interrupción de lectura: la "
    "muestra ya está a 43.38 °C, por debajo de su umbral de cierre. Nunca "
    "hubo ventana de integración para ese punto.",
]


def verificar(ventana: VentanaAmbos, carpeta: Path) -> int:
    fallos = []

    n = len(ventana._advertencias)
    if n != len(MENSAJES):
        fallos.append(f"acumuladas {n}, se esperaban {len(MENSAJES)}")

    progreso = ventana._lbl_progreso.text()
    if str(len(MENSAJES)) not in progreso:
        fallos.append(f"el contador no aparece en la etiqueta: {progreso!r}")

    if "d29922" not in ventana._lbl_progreso.styleSheet():
        fallos.append("la etiqueta de progreso no quedó en ámbar")

    log = ventana._store.csv_path.parent / "advertencias.log" if ventana._store.csv_path else None
    if log is None or not log.exists():
        fallos.append("no se escribió advertencias.log")
    else:
        lineas = [l for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
        if len(lineas) != len(MENSAJES):
            fallos.append(f"advertencias.log tiene {len(lineas)} líneas")
        if lineas and not lineas[0].startswith("["):
            fallos.append("las líneas del log no llevan marca de tiempo")
        print(f"\nadvertencias.log ({log}):")
        for l in lineas:
            print(f"  {l}")

    print("\n" + "=" * 70)
    if fallos:
        print("FALLAS")
        for f in fallos:
            print(f"  - {f}")
    else:
        print("Todo correcto: acumulación, contador, color y archivo en disco.")
    print("=" * 70)
    return 1 if fallos else 0


def main() -> int:
    app = QApplication(sys.argv)
    ventana = VentanaAmbos()
    ventana.show()

    with tempfile.TemporaryDirectory(prefix="prueba_gui_") as tmp:
        carpeta = Path(tmp)
        if not ventana._store.nueva_sesion(nombre="prueba_gui", carpeta_base=carpeta):
            print("No se pudo crear la sesión temporal.")
            return 1

        ventana._lbl_progreso.setText("Secuencia en curso…")
        for mensaje in MENSAJES:
            ventana._medicion.advertencia.emit(mensaje)

        codigo = verificar(ventana, carpeta)

        print("\nAbriendo el diálogo de fin de secuencia. Revise que el botón")
        print("de detalles muestre las cuatro advertencias, y ciérrelo.")
        QTimer.singleShot(0, lambda: ventana._on_secuencia_ok(2))
        app.exec()

    return codigo


if __name__ == "__main__":
    sys.exit(main())
