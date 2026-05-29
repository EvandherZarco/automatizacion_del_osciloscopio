"""
monitoreo.py
Monitoreo continuo de conexión de los tres dispositivos.
Gestiona LEDs, reintentos de reconexión y activación de modo seguro.
Llamado por GUI al iniciar la app. Medición lo pausa/reanuda durante capturas.

La reconexión de cada dispositivo corre en su propio QThread para no bloquear
la GUI, incluso cuando el timeout de hardware (p. ej. VXI-11) es largo.
"""

from __future__ import annotations

import logging
from enum import Enum, auto

from PySide6.QtCore import QObject, Signal, Slot, QTimer, QThread

from app.laser.control_laser import LaserController
from app.osciloscopio.control_osciloscopio import OsciloscopioController
from app.temperatura.temperatura import TempWorker
from app.modo_seguro.modo_seguro import ModoSeguro

logger = logging.getLogger(__name__)

INTERVALO_REPOSO_MS = 5_000
INTERVALO_ENTRE_MS = 1_000
MAX_REINTENTOS = 3


class EstadoMonitoreo(Enum):
    REPOSO = auto()
    ENTRE_MEDICIONES = auto()
    CAPTURANDO = auto()


# ──────────────────────────────────────────────────────────────────────────────


class _ReconexionWorker(QObject):
    """
    Intenta reconectar un dispositivo en un hilo secundario.
    Emite resultado(dispositivo, reconectado) al terminar.
    """

    resultado = Signal(str, bool)

    def __init__(
        self,
        dispositivo: str,
        laser: LaserController,
        oscil: OsciloscopioController,
        temp: TempWorker,
        parent=None,
    ):
        super().__init__(parent)
        self._dispositivo = dispositivo
        self._laser = laser
        self._oscil = oscil
        self._temp = temp

    @Slot()
    def ejecutar(self):
        reconectado = False
        for intento in range(1, MAX_REINTENTOS + 1):
            logger.info(
                "Reconectando %s — intento %d/%d",
                self._dispositivo,
                intento,
                MAX_REINTENTOS,
            )
            reconectado = self._intentar_reconexion()
            if reconectado:
                break

        self.resultado.emit(self._dispositivo, reconectado)

    def _intentar_reconexion(self) -> bool:
        if self._dispositivo == "laser":
            return self._laser.conectar()
        if self._dispositivo == "oscil":
            return self._oscil.conectar()
        if self._dispositivo == "esp32":
            return self._temp.reconectar()
        return False


# ──────────────────────────────────────────────────────────────────────────────


class MonitoreoConexion(QObject):

    laser_led_verde = Signal()
    laser_led_amarillo = Signal()
    laser_led_rojo = Signal()

    oscil_led_verde = Signal()
    oscil_led_amarillo = Signal()
    oscil_led_rojo = Signal()

    esp32_led_verde = Signal()
    esp32_led_amarillo = Signal()
    esp32_led_rojo = Signal()

    ds_led_verde = Signal(int)  # índice 0-3
    ds_led_amarillo = Signal(int)
    ds_led_rojo = Signal(int)

    error_flag_activo = Signal(bool)
    seguridad_activada = Signal(str)

    def __init__(
        self,
        laser: LaserController,
        oscil: OsciloscopioController,
        temp: TempWorker,
        safe: ModoSeguro,
        parent=None,
    ):
        super().__init__(parent)
        self._laser = laser
        self._oscil = oscil
        self._temp = temp
        self._safe = safe
        self._estado = EstadoMonitoreo.REPOSO
        self._error_flag = False
        self._reconectando: set[str] = set()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._ciclo)

    # ──────────────────────────────────────────────────────────────────────────
    # CONTROL EXTERNO
    # ──────────────────────────────────────────────────────────────────────────

    def iniciar(self):
        self._ping_todos()
        self._aplicar_intervalo()
        self._timer.start()

    def detener(self):
        self._timer.stop()

    def pausar_pings(self):
        self._estado = EstadoMonitoreo.CAPTURANDO
        self._timer.stop()

    def reanudar_pings(self):
        self._estado = EstadoMonitoreo.ENTRE_MEDICIONES
        self._aplicar_intervalo()
        self._timer.start()

    def set_estado(self, estado: EstadoMonitoreo):
        self._estado = estado
        self._timer.stop()
        if estado != EstadoMonitoreo.CAPTURANDO:
            self._aplicar_intervalo()
            self._timer.start()

    def pre_chequeo(self) -> bool:
        resultados = self._ping_todos()
        if all(resultados.values()):
            return True
        for dev, ok in resultados.items():
            if not ok:
                self._manejar_desconexion(dev)
        return False

    @property
    def error_flag(self) -> bool:
        return self._error_flag

    # ──────────────────────────────────────────────────────────────────────────
    # LOOP INTERNO
    # ──────────────────────────────────────────────────────────────────────────

    def _ciclo(self):
        if self._estado == EstadoMonitoreo.CAPTURANDO:
            return
        resultados = self._ping_todos()
        for dev, ok in resultados.items():
            if not ok:
                self._manejar_desconexion(dev)

    def _ping_todos(self) -> dict[str, bool]:
        _, sensores, es_fresco = self._temp.consultar()

        for i, ok in enumerate(sensores):
            if es_fresco:
                if ok:
                    self.ds_led_verde.emit(i)
                else:
                    self.ds_led_rojo.emit(i)

        return {
            "laser": self._laser.conectado,
            "oscil": self._oscil.conectado,
            "esp32": es_fresco,
        }

    def _aplicar_intervalo(self):
        intervalo = (
            INTERVALO_ENTRE_MS
            if self._estado == EstadoMonitoreo.ENTRE_MEDICIONES
            else INTERVALO_REPOSO_MS
        )
        self._timer.setInterval(intervalo)

    # ──────────────────────────────────────────────────────────────────────────
    # MANEJO DE DESCONEXIONES — no bloqueante
    # ──────────────────────────────────────────────────────────────────────────

    def _manejar_desconexion(self, dispositivo: str):
        if dispositivo in self._reconectando:
            return

        self._reconectando.add(dispositivo)
        self._set_led_amarillo(dispositivo)
        self._set_error_flag(True)
        logger.warning("Desconexión detectada: %s", dispositivo)

        worker = _ReconexionWorker(dispositivo, self._laser, self._oscil, self._temp)
        thread = QThread(self)
        worker.moveToThread(thread)

        thread.started.connect(worker.ejecutar)
        worker.resultado.connect(self._on_reconexion)
        worker.resultado.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(worker.deleteLater)

        thread.start()

    @Slot(str, bool)
    def _on_reconexion(self, dispositivo: str, reconectado: bool):
        self._reconectando.discard(dispositivo)
        es_critico = dispositivo in ("laser", "oscil")

        if reconectado:
            self._set_led_verde(dispositivo)
            self._set_error_flag(False)
            logger.info("%s reconectado.", dispositivo)
        else:
            self._set_led_rojo(dispositivo)
            logger.error(
                "%s no reconectado tras %d intentos.", dispositivo, MAX_REINTENTOS
            )
            if es_critico:
                self._safe.activar()
                self.seguridad_activada.emit(dispositivo)

    def _set_error_flag(self, valor: bool):
        if self._error_flag != valor:
            self._error_flag = valor
            self.error_flag_activo.emit(valor)

    # ──────────────────────────────────────────────────────────────────────────
    # EMISIÓN DE LEDs
    # ──────────────────────────────────────────────────────────────────────────

    def _set_led_verde(self, dev: str):
        if dev == "laser":
            self.laser_led_verde.emit()
        elif dev == "oscil":
            self.oscil_led_verde.emit()
        elif dev == "esp32":
            self.esp32_led_verde.emit()

    def _set_led_amarillo(self, dev: str):
        if dev == "laser":
            self.laser_led_amarillo.emit()
        elif dev == "oscil":
            self.oscil_led_amarillo.emit()
        elif dev == "esp32":
            self.esp32_led_amarillo.emit()

    def _set_led_rojo(self, dev: str):
        if dev == "laser":
            self.laser_led_rojo.emit()
        elif dev == "oscil":
            self.oscil_led_rojo.emit()
        elif dev == "esp32":
            self.esp32_led_rojo.emit()
