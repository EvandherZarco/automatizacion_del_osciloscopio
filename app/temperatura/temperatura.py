"""
temperatura.py
Lectura continua de temperatura desde ESP32-C3 con cuatro sensores DS18B20.

El ESP32 hace streaming automático cada ~1 s en el formato:
    "25.30,1,1,0,1\\n"
    └ temp_promedio °C · S1 · S2 · S3 · S4  (1 = presente, 0 = ausente)

Este módulo corre en un QThread separado y mantiene en memoria
la lectura más reciente. Otros módulos (Trigger, Medición, GUI)
consultan mediante consultar() sin bloquear el hilo del worker.

Comandos aceptados por el ESP32 (enviar con '\\n'):
    PING  →  responde "PONG\\n"
    STOP  →  detiene el streaming hasta un reset del ESP32
"""

from __future__ import annotations

import time
import serial
import serial.tools.list_ports
from PySide6.QtCore import QObject, Signal, Slot, QMutex, QMutexLocker

BAUD_RATE = 115200
TIMEOUT_LINEA_S = 1.5
TIMEOUT_SILENCIO = 5.0
FRESCURA_MAX_S = 3.0
TEMP_MIN = 10.0
TEMP_MAX = 50.0
ESPERA_PRIMER_S = 5.0


def listar_puertos() -> list[str]:
    """Devuelve los puertos COM disponibles en el sistema."""
    return [p.device for p in serial.tools.list_ports.comports()]


class TempWorker(QObject):
    """
    Worker de temperatura para ESP32-C3 + DS18B20 ×4.
    Diseñado para correr en un QThread independiente.

    Señales:
        conectado(list[bool])  — primera trama válida recibida;
                                 lista con estado de los 4 sensores.
        desconectado()         — ESP32 dejó de responder o falló la conexión.
        trigger(float)         — nueva lectura válida; temperatura promedio en °C.
        error(str)             — descripción de cualquier condición anómala.
    """

    conectado = Signal(list)
    desconectado = Signal()
    trigger = Signal(float)
    error = Signal(str)

    def __init__(self, puerto: str, parent=None):
        super().__init__(parent)
        self._puerto = puerto
        self._serial: serial.Serial | None = None
        self._activo = False

        self._ultima_temp: float | None = None
        self._estado_sensores: list[bool] = [False, False, False, False]
        self._timestamp_lectura: float = 0.0
        self._mutex = QMutex()

    # ── API pública ────────────────────────────────────────────────────────────

    def consultar(self) -> tuple[float | None, list[bool], bool]:
        """
        Devuelve (temperatura, estado_sensores, es_fresco).

        temperatura    : último valor válido leído, o None si no hay datos aún.
        estado_sensores: lista de 4 bool — True si el sensor DS18B20 está presente.
        es_fresco      : True si la lectura tiene menos de FRESCURA_MAX_S segundos.

        Thread-safe — puede llamarse desde cualquier hilo.
        """
        with QMutexLocker(self._mutex):
            temp = self._ultima_temp
            sensores = list(self._estado_sensores)
            ts = self._timestamp_lectura

        es_fresco = temp is not None and (time.monotonic() - ts) < FRESCURA_MAX_S
        return temp, sensores, es_fresco

    def esta_conectado(self) -> bool:
        """
        Retorna True si hay una lectura fresca disponible.
        Thread-safe — puede llamarse desde cualquier hilo.
        """
        _, _, es_fresco = self.consultar()
        return es_fresco

    def reconectar(self) -> bool:
        """
        Verifica si el ESP32 sigue enviando datos frescos.
        Si el worker ya detuvo su loop (desconexión detectada), retorna False.
        Una reconexión real requiere reiniciar el QThread desde la GUI.
        """
        return self.esta_conectado()

    @Slot()
    def detener(self):
        """Señaliza al loop para que termine. Envía STOP al ESP32."""
        self._activo = False
        self._enviar_stop()

    # ── Loop principal ─────────────────────────────────────────────────────────

    @Slot()
    def iniciar(self):
        """
        Abre el puerto serial, espera la primera trama válida y
        entra en el loop de lectura continua.
        Conectar a thread.started para arranque automático.
        """
        if not self._abrir_puerto():
            return

        if not self._esperar_primer_dato():
            self._cerrar_puerto()
            return

        self._activo = True
        ultimo_dato = time.monotonic()

        while self._activo:
            linea = self._leer_linea()

            if linea is None:
                if time.monotonic() - ultimo_dato > TIMEOUT_SILENCIO:
                    self.error.emit(
                        f"ESP32 en {self._puerto} sin respuesta "
                        f"por {TIMEOUT_SILENCIO:.0f} s."
                    )
                    self.desconectado.emit()
                    self._cerrar_puerto()
                    return
                continue

            ultimo_dato = time.monotonic()
            resultado = self._parsear(linea)
            if resultado is None:
                continue

            temp, sensores = resultado

            with QMutexLocker(self._mutex):
                self._ultima_temp = temp
                self._estado_sensores = sensores
                self._timestamp_lectura = time.monotonic()

            self.trigger.emit(temp)

        self._cerrar_puerto()

    # ── Helpers internos ───────────────────────────────────────────────────────

    def _abrir_puerto(self) -> bool:
        try:
            self._serial = serial.Serial(
                port=self._puerto,
                baudrate=BAUD_RATE,
                timeout=TIMEOUT_LINEA_S,
            )
            return True
        except serial.SerialException as exc:
            self.error.emit(f"No se pudo abrir {self._puerto}: {exc}")
            self.desconectado.emit()
            return False

    def _esperar_primer_dato(self) -> bool:
        deadline = time.monotonic() + ESPERA_PRIMER_S
        while time.monotonic() < deadline:
            linea = self._leer_linea()
            if linea is None:
                continue
            resultado = self._parsear(linea)
            if resultado is None:
                continue

            temp, sensores = resultado

            with QMutexLocker(self._mutex):
                self._ultima_temp = temp
                self._estado_sensores = sensores
                self._timestamp_lectura = time.monotonic()

            self.conectado.emit(sensores)

            ausentes = [i + 1 for i, ok in enumerate(sensores) if not ok]
            if ausentes:
                self.error.emit(
                    f"Sensores DS18B20 no detectados: {ausentes}. "
                    "Las mediciones afectadas se guardarán con error_flag=1."
                )
            return True

        self.error.emit(
            f"ESP32 en {self._puerto} no envió datos en {ESPERA_PRIMER_S:.0f} s. "
            "Verificar cable USB y firmware."
        )
        self.desconectado.emit()
        return False

    def _leer_linea(self) -> str | None:
        try:
            raw = self._serial.readline()
            if raw:
                return raw.decode("utf-8", errors="ignore").strip()
        except serial.SerialException as exc:
            self.error.emit(f"Error de lectura serial: {exc}")
        return None

    def _parsear(self, linea: str) -> tuple[float, list[bool]] | None:
        partes = linea.split(",")
        if len(partes) != 5:
            return None
        try:
            temp = float(partes[0])
            sensores = [int(p) == 1 for p in partes[1:]]
        except ValueError:
            return None

        if not (TEMP_MIN <= temp <= TEMP_MAX):
            return None

        return temp, sensores

    def _enviar_stop(self) -> None:
        if self._serial and self._serial.is_open:
            try:
                self._serial.write(b"STOP\n")
            except serial.SerialException:
                pass

    def _cerrar_puerto(self) -> None:
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except serial.SerialException:
                pass
        self._serial = None
