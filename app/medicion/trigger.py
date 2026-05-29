"""
trigger.py
Módulo de trigger para secuencia automática de medición.

Modo por tiempo:
  Emite medir_ahora() cada N segundos durante M mediciones.

Modo por temperatura:
  La muestra se enfría. Por cada temperatura objetivo (de mayor a menor):
    1. Espera que temp ≤ T_obj + 0.1  →  emite iniciar_acumulacion()
    2. Espera que temp ≤ T_obj - 0.1  →  emite detener_y_capturar()

Toda la lógica corre en su propio QThread (iniciar() es el loop bloqueante).
Las señales hacia MedicionWorker son conexiones en cola (queued) automáticamente
porque los dos objetos viven en hilos distintos.
"""

from __future__ import annotations

import time
from PySide6.QtCore import QObject, Signal, Slot

POLL_TEMP_S = 0.5  # intervalo de sondeo de temperatura en modo por temperatura


class TriggerWorker(QObject):

    medir_ahora = Signal()  # modo tiempo: dispara una medición completa
    iniciar_acumulacion = Signal()  # modo temperatura: ACQ:STATE RUN
    detener_y_capturar = Signal()  # modo temperatura: ACQ:STATE STOP + CURVE?
    secuencia_terminada = Signal()  # fin normal o detenida externamente

    def __init__(
        self,
        modo: str,
        intervalo_s: float = 60.0,
        n_mediciones: int = 10,
        t_inicial: float = 35.0,
        t_final: float = 20.0,
        paso: float = 1.0,
        temp_worker=None,
        parent=None,
    ):
        """
        modo        : "tiempo" | "temperatura"
        intervalo_s : segundos entre mediciones (modo tiempo)
        n_mediciones: total de mediciones (modo tiempo)
        t_inicial   : temperatura de inicio en °C (modo temperatura, mayor valor)
        t_final     : temperatura de fin en °C (modo temperatura, menor valor)
        paso        : decremento entre objetivos en °C (modo temperatura)
        temp_worker : instancia de TempWorker — debe implementar consultar()
        """
        super().__init__(parent)
        self._modo = modo
        self._intervalo_s = intervalo_s
        self._n_mediciones = n_mediciones
        self._t_inicial = t_inicial
        self._t_final = t_final
        self._paso = paso
        self._temp_worker = temp_worker
        self._activo = False

    @Slot()
    def detener(self):
        """Señaliza al loop para que termine en la próxima iteración."""
        self._activo = False

    @Slot()
    def iniciar(self):
        """
        Loop principal del trigger.
        Llamado al conectar thread.started con este slot,
        o directamente desde el QThread si se prefiere.
        """
        self._activo = True

        if self._modo == "tiempo":
            self._loop_tiempo()
        else:
            self._loop_temperatura()

        self.secuencia_terminada.emit()

    # ── Modo por tiempo ────────────────────────────────────────────────────────

    def _loop_tiempo(self):
        for _ in range(self._n_mediciones):
            if not self._activo:
                return

            self.medir_ahora.emit()

            t_fin = time.monotonic() + self._intervalo_s
            while time.monotonic() < t_fin:
                if not self._activo:
                    return
                time.sleep(0.2)

    # ── Modo por temperatura ───────────────────────────────────────────────────

    def _loop_temperatura(self):
        for t_obj in self._generar_objetivos():
            if not self._activo:
                return

            if not self._esperar_umbral(t_obj + 0.1):
                return

            self.iniciar_acumulacion.emit()

            if not self._esperar_umbral(t_obj - 0.1):
                return

            self.detener_y_capturar.emit()

    def _generar_objetivos(self) -> list[float]:
        """
        Lista de temperaturas objetivo de mayor a menor.
        Ejemplo: t_inicial=35, t_final=20, paso=1  →  [35, 34, 33, ..., 20]
        """
        objetivos = []
        t = self._t_inicial
        while t >= self._t_final - 1e-9:
            objetivos.append(round(t, 4))
            t = round(t - self._paso, 4)
        return objetivos

    def _esperar_umbral(self, umbral: float) -> bool:
        """
        Espera a que la temperatura baje hasta umbral (temp ≤ umbral).
        Devuelve False si se cancela con detener().
        """
        while self._activo:
            temp = self._leer_temperatura()
            if temp is not None and temp <= umbral:
                return True
            time.sleep(POLL_TEMP_S)
        return False

    def _leer_temperatura(self) -> float | None:
        if self._temp_worker is None:
            return None
        temp, _, es_fresco = self._temp_worker.consultar()
        if not es_fresco:
            return None
        return temp
