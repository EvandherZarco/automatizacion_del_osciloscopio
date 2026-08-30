"""
trigger.py
Módulo de trigger para secuencia automática de medición.

Modo por tiempo:
  Emite medir_ahora() cada N segundos durante M mediciones.

Modo por temperatura:
  La muestra se enfría. Por cada temperatura objetivo (de mayor a menor):
    1. Espera que temp ≤ T_obj + 0.1  →  emite iniciar_acumulacion()
    2. Espera que temp ≤ T_obj - 0.1  →  emite detener_y_capturar()

  La lectura se filtra con mediana móvil y cada cruce de umbral requiere
  confirmaciones consecutivas, de modo que el ruido del sensor no dispare
  ni cierre una ventana de integración antes de tiempo.

  Los objetivos cuya ventana ya quedó por encima de la temperatura inicial
  de la muestra se omiten: nunca podrían observarse y generarían ventanas
  de duración nula.

  La ausencia sostenida de lecturas frescas emite advertencia y, si se
  prolonga, termina la secuencia en lugar de esperar indefinidamente.

Toda la lógica corre en su propio QThread (iniciar() es el loop bloqueante).
Las señales hacia MedicionWorker son conexiones en cola (queued) automáticamente
porque los dos objetos viven en hilos distintos.
"""

from __future__ import annotations

import time
from collections import deque
from statistics import median

from PySide6.QtCore import QObject, Signal, Slot

POLL_TEMP_S = 0.5  # intervalo de sondeo de temperatura en modo por temperatura
VENTANA_MEDIANA = 3  # lecturas usadas por el filtro de mediana móvil
CONFIRMACIONES_UMBRAL = 2  # lecturas consecutivas que validan un cruce
TIMEOUT_ADVERTENCIA_S = 30.0  # sin lectura fresca: se avisa
TIMEOUT_ABORTO_S = 300.0  # sin lectura fresca: se termina la secuencia
MARGEN_UMBRAL = 0.1  # semiancho de la banda alrededor del objetivo


class TriggerWorker(QObject):

    medir_ahora = Signal()  # modo tiempo: dispara una medición completa
    iniciar_acumulacion = Signal()  # modo temperatura: ACQ:STATE RUN
    detener_y_capturar = Signal()  # modo temperatura: ACQ:STATE STOP + CURVE?
    secuencia_terminada = Signal()  # fin normal o detenida externamente
    intervalo_excedido = Signal(float)  # segundos que la captura excedió el intervalo
    advertencia = Signal(str)  # incidencias no fatales durante la secuencia

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
        self._capturando = False
        self._buffer_temp: deque[float] = deque(maxlen=VENTANA_MEDIANA)

    @Slot()
    def detener(self):
        """Señaliza al loop para que termine en la próxima iteración."""
        self._activo = False

    @Slot()
    def on_captura_iniciando(self):
        """Recibe la señal de MedicionWorker cuando arranca una captura."""
        self._capturando = True

    @Slot()
    def on_captura_terminada(self):
        """Recibe la señal de MedicionWorker cuando la captura termina."""
        self._capturando = False

    @Slot()
    def iniciar(self):
        """
        Loop principal del trigger.
        Llamado al conectar thread.started con este slot,
        o directamente desde el QThread si se prefiere.
        """
        self._activo = True
        self._buffer_temp.clear()

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

            if not self._esperar_fin_de_captura():
                return

            t0 = time.monotonic()
            self.medir_ahora.emit()

            if not self._esperar_fin_de_captura():
                return

            tiempo_restante = self._intervalo_s - (time.monotonic() - t0)
            if tiempo_restante < 0:
                self.intervalo_excedido.emit(abs(tiempo_restante))
            else:
                t_fin = time.monotonic() + tiempo_restante
                while time.monotonic() < t_fin:
                    if not self._activo:
                        return
                    time.sleep(0.2)

    # ── Modo por temperatura ───────────────────────────────────────────────────

    def _loop_temperatura(self):
        objetivos = self._descartar_objetivos_rebasados(self._generar_objetivos())

        if not objetivos:
            self.advertencia.emit(
                "La muestra ya está por debajo de todos los objetivos. "
                "No hay puntos que medir."
            )
            return

        for t_obj in objetivos:
            if not self._activo:
                return

            if not self._esperar_umbral(t_obj + MARGEN_UMBRAL):
                return

            self.iniciar_acumulacion.emit()

            if not self._esperar_umbral(t_obj - MARGEN_UMBRAL):
                return

            self.detener_y_capturar.emit()

            if not self._esperar_fin_de_captura():
                return

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

    def _descartar_objetivos_rebasados(self, objetivos: list[float]) -> list[float]:
        """
        Elimina los objetivos cuya ventana de entrada ya quedó por encima de la
        temperatura actual de la muestra. Sin este filtro, esos objetivos
        satisfacen ambos umbrales de inmediato y producen capturas con ventana
        de integración de duración nula.
        """
        temp = self._esperar_primera_lectura()
        if temp is None:
            return objetivos

        alcanzables = [t for t in objetivos if temp > t + MARGEN_UMBRAL]

        if len(alcanzables) < len(objetivos):
            omitidos = objetivos[: len(objetivos) - len(alcanzables)]
            lista = ", ".join(f"{t:.1f}" for t in omitidos)
            self.advertencia.emit(
                f"La muestra está a {temp:.2f} °C. Se omiten los objetivos "
                f"por encima de esa temperatura: {lista} °C."
            )

        return alcanzables

    def _esperar_primera_lectura(self) -> float | None:
        """
        Obtiene la primera temperatura filtrada del arranque. Devuelve None si
        no llega ninguna dentro del plazo de advertencia.
        """
        limite = time.monotonic() + TIMEOUT_ADVERTENCIA_S
        while self._activo and time.monotonic() < limite:
            temp = self._leer_temperatura_estable()
            if temp is not None:
                return temp
            time.sleep(POLL_TEMP_S)
        return None

    def _esperar_umbral(self, umbral: float) -> bool:
        """
        Espera a que la temperatura filtrada baje hasta umbral (temp ≤ umbral),
        confirmada por lecturas consecutivas.

        Devuelve False si se cancela con detener() o si se pierde la lectura
        de temperatura durante más de TIMEOUT_ABORTO_S.
        """
        confirmaciones = 0
        ultimo_dato = time.monotonic()
        advertido = False

        while self._activo:
            temp = self._leer_temperatura_estable()
            ahora = time.monotonic()

            if temp is None:
                silencio = ahora - ultimo_dato
                if silencio > TIMEOUT_ABORTO_S:
                    self.advertencia.emit(
                        f"Sin lectura de temperatura durante {silencio:.0f} s. "
                        "Se termina la secuencia."
                    )
                    self._activo = False
                    return False
                if silencio > TIMEOUT_ADVERTENCIA_S and not advertido:
                    self.advertencia.emit(
                        "Lectura de temperatura interrumpida. "
                        "La secuencia continúa en espera."
                    )
                    advertido = True
                time.sleep(POLL_TEMP_S)
                continue

            ultimo_dato = ahora
            if advertido:
                self.advertencia.emit("Lectura de temperatura restablecida.")
                advertido = False

            if temp <= umbral:
                confirmaciones += 1
                if confirmaciones >= CONFIRMACIONES_UMBRAL:
                    return True
            else:
                confirmaciones = 0

            time.sleep(POLL_TEMP_S)

        return False

    def _esperar_fin_de_captura(self) -> bool:
        """Bloquea mientras MedicionWorker tenga una captura en curso."""
        while self._capturando and self._activo:
            time.sleep(0.1)
        return self._activo

    # ── Lectura de temperatura ─────────────────────────────────────────────────

    def _leer_temperatura_estable(self) -> float | None:
        """
        Lectura filtrada con mediana móvil. Devuelve None mientras no haya
        lecturas frescas suficientes para llenar la ventana del filtro.
        """
        temp = self._leer_temperatura()
        if temp is None:
            return None

        self._buffer_temp.append(temp)
        if len(self._buffer_temp) < VENTANA_MEDIANA:
            return None

        return median(self._buffer_temp)

    def _leer_temperatura(self) -> float | None:
        if self._temp_worker is None:
            return None
        temp, _, es_fresco = self._temp_worker.consultar()
        if not es_fresco:
            return None
        return temp
