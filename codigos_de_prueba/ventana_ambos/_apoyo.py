"""
_apoyo.py
Utilidades compartidas por las pruebas de codigos_de_prueba/ventana_ambos/.
No es una prueba en sí — la importan los probar_*.py de esta carpeta.

Arma un entorno Qt sin hardware, igual en espíritu a
codigos_de_prueba/probar_config_usuario.py pero para VentanaAmbos:

  - QT_QPA_PLATFORM=offscreen y un APPDATA temporal (no toca el JSON real
    del usuario).
  - Un vxi11 falso instalado en sys.modules ANTES de importar
    app.osciloscopio.control_osciloscopio (que hace `import vxi11` a nivel
    de módulo). No es solo para no depender del osciloscopio real: si el
    host configurado no responde, python-vxi11 busca el servicio VXI-11
    por RPC sin que el código de la app le fije un timeout a esa búsqueda
    inicial, así que conectar a un host inalcanzable podría colgar la
    prueba en vez de fallar rápido — que es justamente lo que se quiere
    simular (el osciloscopio desconectado).
  - El láser (DLL de Windows vía ctypes) y el ESP32 (puerto serie) no se
    reemplazan: LaserController.conectar() ya captura cualquier excepción
    al cargar/usar la DLL (incluido AttributeError en Linux, donde
    ctypes.WinDLL no existe) y TempWorker falla rápido si el puerto
    configurado no existe. En ambos casos el resultado observable es el
    mismo que sin hardware conectado: fallan, no cuelgan.
  - Los QMessageBox.warning/question/critical/information, y las cajas
    construidas como instancia (QMessageBox(...).exec()), quedan
    interceptados: no bloquean esperando un clic real y registran
    (tipo, título, texto) de cada una para que la prueba pueda revisar
    qué se mostró.
  - Los QFileDialog.getExistingDirectory/getOpenFileName quedan
    interceptados de la misma forma y responden como si el usuario
    cancelara: una prueba que llegue a uno por error falla en vez de
    quedarse colgada esperando una carpeta.

Requiere las mismas dependencias que la propia app (PySide6, pyqtgraph,
numpy, scipy) — las del venv del proyecto. No instala nada nuevo: el vxi11 falso
sólo reemplaza el módulo en sys.modules durante esta ejecución.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
APPDATA_TEMPORAL = tempfile.mkdtemp(prefix="ventana_ambos_prueba_")
os.environ["APPDATA"] = APPDATA_TEMPORAL


class _InstrumentoFalso:
    """Sustituye a vxi11.Instrument: en esta prueba nunca hay osciloscopio real."""

    def __init__(self, host, *_a, **_kw):
        self.host = host
        self.timeout = 10
        raise ConnectionError(f"[prueba sin hardware] no hay osciloscopio en {host}")

    def ask(self, *_a, **_kw):
        raise ConnectionError("[prueba sin hardware] sin conexión")

    def write(self, *_a, **_kw):
        raise ConnectionError("[prueba sin hardware] sin conexión")

    def read_raw(self):
        raise ConnectionError("[prueba sin hardware] sin conexión")

    def close(self):
        pass


def _instalar_vxi11_falso() -> None:
    if getattr(sys.modules.get("vxi11"), "_ES_FALSO_DE_PRUEBA", False):
        return
    modulo = types.ModuleType("vxi11")
    modulo.Instrument = _InstrumentoFalso
    modulo.Vxi11Exception = Exception
    modulo._ES_FALSO_DE_PRUEBA = True
    sys.modules["vxi11"] = modulo


_instalar_vxi11_falso()

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402 (después del stub)


def app_qt() -> QApplication:
    return QApplication.instance() or QApplication(sys.argv)


class CajasCapturadas:
    """
    Reemplaza los QMessageBox estáticos y QMessageBox(...).exec()/.exec_()
    por versiones que no esperan un clic real y registran qué se mostró.
    instalar() debe llamarse antes de construir la ventana bajo prueba
    (los botones "Aplicar"/"Iniciar secuencia"/etc. disparan diálogos
    modales que, sin esto, dejarían la prueba colgada esperando un clic).
    """

    def __init__(self):
        self.llamadas: list[tuple[str, str, str]] = []
        self._originales: dict[str, object] = {}
        self._dialogos_originales: dict[str, object] = {}

    def _fabricar(self, tipo: str, boton_devuelto):
        def _f(*a, **_kw):
            titulo = str(a[1]) if len(a) > 1 else ""
            texto = str(a[2]) if len(a) > 2 else ""
            self.llamadas.append((tipo, titulo, texto))
            return boton_devuelto
        return staticmethod(_f)

    def _fabricar_dialogo(self, tipo: str, respuesta):
        def _f(*a, **_kw):
            titulo = str(a[1]) if len(a) > 1 else ""
            self.llamadas.append((tipo, titulo, ""))
            return respuesta
        return staticmethod(_f)

    def instalar(self) -> None:
        self._dialogos_originales = {
            "getExistingDirectory": QFileDialog.getExistingDirectory,
            "getOpenFileName": QFileDialog.getOpenFileName,
        }
        QFileDialog.getExistingDirectory = self._fabricar_dialogo("carpeta", "")
        QFileDialog.getOpenFileName = self._fabricar_dialogo("archivo", ("", ""))
        self._originales = {
            "warning": QMessageBox.warning,
            "critical": QMessageBox.critical,
            "question": QMessageBox.question,
            "information": QMessageBox.information,
            "exec": QMessageBox.exec,
            "exec_": QMessageBox.exec_,
        }
        QMessageBox.warning = self._fabricar("warning", QMessageBox.Ok)
        QMessageBox.critical = self._fabricar("critical", QMessageBox.Ok)
        QMessageBox.question = self._fabricar("question", QMessageBox.Yes)
        QMessageBox.information = self._fabricar("information", QMessageBox.Ok)
        QMessageBox.exec = lambda _self, *a, **kw: QMessageBox.Ok
        QMessageBox.exec_ = lambda _self, *a, **kw: QMessageBox.Ok

    def desinstalar(self) -> None:
        for nombre, original in self._originales.items():
            setattr(QMessageBox, nombre, original)
        for nombre, original in self._dialogos_originales.items():
            setattr(QFileDialog, nombre, original)

    def limpiar(self) -> None:
        self.llamadas.clear()

    def textos(self, tipo: str | None = None) -> list[str]:
        """Título + cuerpo concatenados de cada llamada capturada."""
        return [f"{t} {c}" for (tp, t, c) in self.llamadas if tipo is None or tp == tipo]


def crear_ventana_ambos(cajas: CajasCapturadas):
    """
    Construye una VentanaAmbos real y deja correr el loop de eventos unos
    ciclos para que los hilos de conexión inicial (láser/osciloscopio/ESP32,
    todos sin hardware real) terminen de fallar y liberen sus hilos.
    """
    cajas.instalar()
    from app.gui.ventana_ambos import VentanaAmbos
    app = app_qt()
    ventana = VentanaAmbos()
    for _ in range(40):
        app.processEvents()
    return ventana


def limpiar() -> None:
    shutil.rmtree(APPDATA_TEMPORAL, ignore_errors=True)
