"""
medicion.py
Orquestador de la secuencia automática de medición.

Contiene dos clases:

  MedicionWorker — lógica de captura; vive en un QThread propio.
  Medicion       — fachada pública que la GUI usa directamente.
                   Crea y gestiona los QThreads de worker y trigger.

Modo por tiempo:
  Trigger emite medir_ahora() cada N segundos.
  Worker hace una captura completa (NUMAVG disparos, polling hasta fin).

Modo por temperatura:
  Trigger emite iniciar_acumulacion() cuando temp ≤ T_obj + 0.1.
  Trigger emite detener_y_capturar()  cuando temp ≤ T_obj - 0.1.
  Worker controla la ventana de integración del osciloscopio.
  pulsos_estimados = (t_fin - t_inicio) × LASER_HZ se guarda en CSV.

El láser corre continuo toda la secuencia: START en iniciar_secuencia(),
Modo Seguro al finalizar o abortar. No hay START/STOP por medición.
"""

from __future__ import annotations
from app.osciloscopio.control_osciloscopio import NUMAVG_TIEMPO

import time
from datetime import datetime

from PySide6.QtCore import QObject, Signal, Slot, QThread, QMetaObject, Qt

from app.almacenamiento.almacenamiento import Almacenamiento, PaqueteMedicion
from app.medicion.trigger import TriggerWorker

LASER_HZ = 10.0


class MedicionWorker(QObject):

    listo = Signal()  # iniciar_secuencia OK — el trigger puede arrancar
    captura_iniciando = Signal()  # avisa al monitor para pausar pings
    captura_terminada = Signal()  # avisa al monitor para reanudar pings
    medicion_completada = Signal(str, int)  # (medicion_id, n_con_flag_acumulado)
    secuencia_terminada = Signal(int, int)  # (total_realizadas, total_con_flag)
    secuencia_abortada = Signal(str)  # motivo del aborto
    error = Signal(str)

    def __init__(
        self,
        modo: str,
        laser_ctrl=None,
        oscil_ctrl=None,
        temp_worker=None,
        almacenamiento: Almacenamiento | None = None,
        modo_seguro=None,
        numavg_tiempo: int = 100,
        n_mediciones: int = 10,
        parent=None,
    ):
        super().__init__(parent)
        self._modo = modo
        self._laser = laser_ctrl
        self._oscil = oscil_ctrl
        self._temp = temp_worker
        self._storage = almacenamiento
        self._safe = modo_seguro
        self._numavg_tiempo = numavg_tiempo
        self._n_mediciones = n_mediciones

        self._activo = False
        self._realizadas = 0
        self._con_flag = 0
        self._t_inicio_acum: float | None = None
        self._intervalo_excedido: float = 0.0

    @Slot()
    def detener(self):
        self._activo = False

    @Slot()
    def iniciar_secuencia(self):
        """
        Enciende el láser y configura el osciloscopio.
        Emite listo() si todo va bien — el trigger arranca al recibirlo.
        """
        self._activo = True
        self._realizadas = 0
        self._con_flag = 0

        if not self._laser_start():
            return

        if self._modo == "tiempo":
            if not self._oscil.configurar_modo_tiempo(self._numavg_tiempo):
                self._abortar("Error al configurar el osciloscopio para modo tiempo.")
                return
        else:
            if not self._oscil.configurar_modo_temperatura():
                self._abortar(
                    "Error al configurar el osciloscopio para modo temperatura."
                )
                return

        self.listo.emit()

    # ── Slots del Trigger ─────────────────────────────────────────────────────

    @Slot()
    def on_medir_ahora(self):
        """Modo tiempo: captura completa en un solo paso."""
        if not self._activo:
            return
        self.captura_iniciando.emit()
        captura = self._oscil.capturar_modo_tiempo()
        self.captura_terminada.emit()
        self._procesar_captura(captura, pulsos_estimados=None)

    @Slot()
    def on_iniciar_acumulacion(self):
        """Modo temperatura: abre la compuerta del osciloscopio."""
        if not self._activo:
            return
        self.captura_iniciando.emit()
        ok = self._oscil.acq_run()
        if ok:
            self._t_inicio_acum = time.monotonic()
        else:
            self.captura_terminada.emit()
            self.error.emit("Error al iniciar ACQ:STATE RUN en modo temperatura.")

    @Slot()
    def on_detener_y_capturar(self):
        """Modo temperatura: cierra la compuerta y captura."""
        if not self._activo:
            return

        t_fin = time.monotonic()
        t_inicio = self._t_inicio_acum if self._t_inicio_acum is not None else t_fin
        pulsos = int((t_fin - t_inicio) * LASER_HZ)
        self._t_inicio_acum = None

        captura = self._oscil.acq_stop_and_capture()
        self.captura_terminada.emit()
        self._procesar_captura(captura, pulsos_estimados=pulsos)

    @Slot(float)
    def on_intervalo_excedido(self, exceso_s: float):
        self._intervalo_excedido = exceso_s

    @Slot()
    def on_secuencia_terminada(self):
        """El Trigger señaliza que terminó su loop."""
        self._finalizar()

    # ── Procesamiento de captura ──────────────────────────────────────────────

    def _procesar_captura(self, captura, pulsos_estimados: int | None):
        error_flag = 0
        errores: list[str] = []

        if captura is None:
            error_flag = 1
            errores.append("captura fallida")
            self.error.emit("Captura fallida — se registra con error_flag=1.")

        temp, _, es_fresco = self._leer_temperatura()
        if not es_fresco:
            error_flag = 1
            errores.append("temperatura no detectada")

        if self._intervalo_excedido > 0:
            errores.append(
                f"captura excedió el intervalo por {self._intervalo_excedido:.1f} s"
            )
            self._intervalo_excedido = 0.0

        error_desc = "; ".join(errores)

        mid = None
        if captura is not None:
            paquete = PaqueteMedicion(
                timestamp=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                temperatura=temp if temp is not None else 0.0,
                modo=self._modo,
                wfmpre=captura.wfmpre,
                raw_data=captura.raw_data,
                error_flag=error_flag,
                error_desc=error_desc,
                pulsos_estimados=pulsos_estimados,
            )
            mid = self._storage.guardar(paquete)

        self._realizadas += 1
        if error_flag:
            self._con_flag += 1

        self.medicion_completada.emit(mid or "", self._con_flag)

    # ── Cierre de secuencia ───────────────────────────────────────────────────

    def _finalizar(self):
        self._activo = False
        self._safe.activar()
        self.secuencia_terminada.emit(self._realizadas, self._con_flag)

    def _abortar(self, msg: str):
        self._activo = False
        self.error.emit(msg)
        self._safe.activar()
        self.secuencia_abortada.emit(msg)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _laser_start(self) -> bool:
        if not self._laser.start():
            self._abortar("No se pudo iniciar el láser. Secuencia abortada.")
            return False
        return True

    def _leer_temperatura(self) -> tuple[float | None, list, bool]:
        if self._temp is None:
            return None, [False] * 4, False
        return self._temp.consultar()


# ══════════════════════════════════════════════════════════════════════════════


class Medicion(QObject):
    """
    Fachada pública de medición para la GUI.

    Gestiona el ciclo de vida completo de MedicionWorker y TriggerWorker,
    incluyendo sus QThreads. La GUI solo necesita llamar iniciar() y detener().
    """

    medicion_guardada = Signal(str, int)  # (medicion_id, n_con_flag_acumulado)
    secuencia_ok = Signal(int)  # n_total_con_flag al terminar
    secuencia_abortada = Signal(str)  # motivo del aborto

    def __init__(
        self,
        laser_ctrl,
        oscil_ctrl,
        temp_worker,
        almacenamiento: Almacenamiento,
        modo_seguro,
        monitor,
        parent=None,
    ):
        super().__init__(parent)
        self._laser = laser_ctrl
        self._oscil = oscil_ctrl
        self._temp = temp_worker
        self._store = almacenamiento
        self._safe = modo_seguro
        self._monitor = monitor

        self._worker: MedicionWorker | None = None
        self._trigger: TriggerWorker | None = None
        self._worker_thread: QThread | None = None
        self._trigger_thread: QThread | None = None

    def iniciar(
        self,
        modo: str,
        intervalo: float = 60.0,
        n_mediciones: int = 10,
        t_inicial: float = 35.0,
        t_final: float = 20.0,
        paso: float = 1.0,
    ):
        """
        Construye worker y trigger, los mueve a sus QThreads y arranca la secuencia.

        El trigger no arranca hasta que worker.listo() confirma que el láser
        y el osciloscopio están configurados correctamente.
        """
        self._limpiar_threads()

        worker = MedicionWorker(
            modo=modo,
            laser_ctrl=self._laser,
            oscil_ctrl=self._oscil,
            temp_worker=self._temp,
            almacenamiento=self._store,
            modo_seguro=self._safe,
            numavg_tiempo=NUMAVG_TIEMPO,
            n_mediciones=n_mediciones,
        )

        trigger = TriggerWorker(
            modo=modo,
            intervalo_s=intervalo,
            n_mediciones=n_mediciones,
            t_inicial=t_inicial,
            t_final=t_final,
            paso=paso,
            temp_worker=self._temp,
        )

        worker_thread = QThread(self)
        trigger_thread = QThread(self)

        worker.moveToThread(worker_thread)
        trigger.moveToThread(trigger_thread)

        # Trigger → Worker (queued por estar en hilos distintos)
        trigger.medir_ahora.connect(worker.on_medir_ahora)
        trigger.iniciar_acumulacion.connect(worker.on_iniciar_acumulacion)
        trigger.detener_y_capturar.connect(worker.on_detener_y_capturar)
        trigger.secuencia_terminada.connect(worker.on_secuencia_terminada)

        # Worker → Trigger: bloqueo de solapamiento en modo tiempo
        worker.captura_iniciando.connect(trigger.on_captura_iniciando)
        worker.captura_terminada.connect(trigger.on_captura_terminada)

        # Trigger → Worker: aviso de intervalo excedido
        trigger.intervalo_excedido.connect(worker.on_intervalo_excedido)

        # Worker → fachada
        worker.medicion_completada.connect(self._on_medicion_completada)
        worker.secuencia_terminada.connect(self._on_secuencia_terminada)
        worker.secuencia_abortada.connect(self._on_secuencia_abortada)

        # Monitor: pausar/reanudar pings alrededor de cada captura
        worker.captura_iniciando.connect(self._monitor.pausar_pings)
        worker.captura_terminada.connect(self._monitor.reanudar_pings)

        # Worker listo → arranca el hilo del trigger
        worker.listo.connect(trigger_thread.start)
        trigger_thread.started.connect(trigger.iniciar)

        # Limpieza de hilos al terminar
        trigger.secuencia_terminada.connect(trigger_thread.quit)
        worker.secuencia_terminada.connect(worker_thread.quit)
        worker.secuencia_abortada.connect(trigger_thread.quit)
        worker.secuencia_abortada.connect(worker_thread.quit)

        self._worker = worker
        self._trigger = trigger
        self._worker_thread = worker_thread
        self._trigger_thread = trigger_thread

        worker_thread.start()
        QMetaObject.invokeMethod(worker, "iniciar_secuencia", Qt.QueuedConnection)

    def detener(self):
        if self._trigger is not None:
            self._trigger._activo = False
        if self._worker is not None:
            self._worker._activo = False
        self._oscil.cancelar_espera()

    # ── Slots internos ────────────────────────────────────────────────────────

    @Slot(str, int)
    def _on_medicion_completada(self, medicion_id: str, con_flag: int):
        self.medicion_guardada.emit(medicion_id, con_flag)

    @Slot(int, int)
    def _on_secuencia_terminada(self, total: int, con_flag: int):
        self.secuencia_ok.emit(con_flag)

    @Slot(str)
    def _on_secuencia_abortada(self, motivo: str):
        self.secuencia_abortada.emit(motivo)

    def _limpiar_threads(self):
        for thread in (self._worker_thread, self._trigger_thread):
            if thread is not None and thread.isRunning():
                thread.quit()
                thread.wait(3000)
        self._worker = None
        self._trigger = None
        self._worker_thread = None
        self._trigger_thread = None
