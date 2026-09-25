"""
temperatura.py
Lectura continua de temperatura desde ESP32 WROOM-32 con cuatro sensores DS18B20.

El ESP32 hace streaming automático cada ~1 s en el formato:
    "21.47,21.44,21.69,21.25,21.50\\n"
    └ temp_promedio °C · S1 · S2 · S3 · S4  (temperatura de cada sensor, °C)

La presencia de cada sensor se deduce del valor: un DS18B20 ausente o
en falla reporta NaN o el artefacto de 85 °C, ambos fuera del rango válido.

Este módulo corre en un QThread separado y mantiene en memoria
la lectura más reciente. Otros módulos (Trigger, Medición, GUI)
consultan mediante consultar() sin bloquear el hilo del worker;
consultar_sensores() entrega además la lectura de cada sensor y si
llegó idéntica a la de la trama anterior.

Comandos aceptados por el ESP32 (enviar con '\\n'):
    PING   →  responde "PONG\\n"
    START  →  reanuda el streaming con una lectura inmediata
    STOP   →  detiene el streaming hasta un START o un reset del ESP32

El puerto se abre siempre con abrir_serial(): DTR y RTS en bajo para no
disparar el circuito de auto-reset de la placa, y START al abrir porque
la sesión anterior terminó con STOP.
"""

from __future__ import annotations

import logging
import time
import serial
import serial.tools.list_ports
from PySide6.QtCore import QObject, Signal, Slot, QMutex, QMutexLocker

from app import config_usuario

logger = logging.getLogger(__name__)

BAUD_RATE = 115200
TIMEOUT_LINEA_S = 1.5
TIMEOUT_SILENCIO = 5.0
FRESCURA_MAX_S = 3.0
TEMP_MIN = 10.0
TEMP_MAX = 50.0
ESPERA_PRIMER_S = 5.0
ESPERA_REINTENTO_S = 2.5


def listar_puertos() -> list[str]:
    """Devuelve los puertos COM disponibles en el sistema."""
    return [p.device for p in serial.tools.list_ports.comports()]


def abrir_serial(puerto: str, timeout: float) -> serial.Serial:
    """
    Abre el puerto del ESP32 sin reiniciarlo y reanuda el streaming.

    DTR y RTS se fijan en bajo antes de open() para que la apertura no
    reinicie la placa ni la deje en modo descarga. Luego se envía START,
    ya que el ESP32 sigue detenido si la última sesión le mandó STOP.
    """
    ser = serial.Serial()
    ser.port = puerto
    ser.baudrate = BAUD_RATE
    ser.timeout = timeout
    ser.dtr = False
    ser.rts = False
    ser.open()
    try:
        ser.write(b"START\n")
    except (serial.SerialException, OSError):
        ser.close()
        raise
    return ser


def parsear_trama(linea: str) -> tuple[float, list[bool]] | None:
    """
    Interpreta una línea del ESP32: promedio y cuatro lecturas en °C.
    Devuelve (temperatura_promedio, presencia_de_cada_sensor) o None si la
    línea no tiene el formato esperado o el promedio está fuera de rango.
    """
    partes = linea.split(",")
    if len(partes) != 5:
        return None
    try:
        temp = float(partes[0])
        lecturas = [float(p) for p in partes[1:]]
    except ValueError:
        return None

    if not (TEMP_MIN <= temp <= TEMP_MAX):
        return None

    sensores = [TEMP_MIN <= t <= TEMP_MAX for t in lecturas]
    return temp, sensores


class TempWorker(QObject):
    """
    Worker de temperatura para ESP32 WROOM-32 + DS18B20 ×4.
    Diseñado para correr en un QThread independiente.

    El puerto se toma de la configuración vigente (config_usuario) cada vez
    que se inicia o reconecta, así un cambio hecho desde la GUI aplica en la
    siguiente conexión sin reiniciar la aplicación.

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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._puerto = config_usuario.obtener("TEMP_COM_PORT")
        self._serial: serial.Serial | None = None
        self._activo = False

        self._ultima_temp: float | None = None
        self._estado_sensores: list[bool] = [False, False, False, False]
        self._lecturas: list[float | None] = [None, None, None, None]
        self._repetidos: list[bool | None] = [None, None, None, None]
        self._crudos_previos: list[str | None] = [None, None, None, None]
        self._timestamp_lectura: float = 0.0
        self._mutex = QMutex()

    # ── API pública ────────────────────────────────────────────────────────────

    @property
    def puerto(self) -> str:
        """Puerto usado en la conexión más reciente."""
        return self._puerto

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

    def consultar_sensores(self) -> tuple[list[float | None], list[bool | None], bool]:
        """
        Devuelve (lecturas, repetidos, es_fresco) de la última trama válida.

        lecturas : temperatura de S1–S4 en °C; None si el sensor no está presente.
        repetidos: True si el valor del sensor llegó idéntico, carácter por
                   carácter, al de la trama válida anterior; None si el
                   sensor no está presente.
        es_fresco: mismo criterio que consultar().

        Thread-safe — puede llamarse desde cualquier hilo.
        """
        with QMutexLocker(self._mutex):
            temp = self._ultima_temp
            lecturas = list(self._lecturas)
            repetidos = list(self._repetidos)
            ts = self._timestamp_lectura

        es_fresco = temp is not None and (time.monotonic() - ts) < FRESCURA_MAX_S
        return lecturas, repetidos, es_fresco

    def esta_conectado(self) -> bool:
        """
        Retorna True si hay una lectura fresca disponible.
        Thread-safe — puede llamarse desde cualquier hilo.
        """
        _, _, es_fresco = self.consultar()
        return es_fresco

    def reconectar(self) -> bool:
        """
        Verifica si el ESP32 volvió a responder en el puerto configurado.

        Si ya hay lecturas frescas no hace nada. En caso contrario reabre
        el puerto configurado y espera una trama válida; el puerto se cierra
        antes de retornar para que el loop de lectura pueda tomarlo al
        reiniciarse. Un puerto reasignado por el sistema operativo no se
        detecta aquí: ese caso se resuelve desde la GUI.
        """
        if self.esta_conectado():
            return True
        puerto = config_usuario.obtener("TEMP_COM_PORT")
        if not self._puerto_responde(puerto):
            return False
        self._puerto = puerto
        return True

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
        self._activo = True
        self._puerto = config_usuario.obtener("TEMP_COM_PORT")
        self._crudos_previos = [None, None, None, None]
        if not self._abrir_puerto():
            return

        try:
            if not self._esperar_primer_dato():
                return

            ultimo_dato = time.monotonic()

            while self._activo:
                linea = self._leer_linea()
                # ultimo_dato solo avanza con una trama que efectivamente se
                # interpretó: una racha de bytes corruptos o mal formados no
                # es silencio, pero tampoco es una lectura viva, y antes no
                # activaba TIMEOUT_SILENCIO — el hilo se quedaba girando sin
                # rendirse nunca.
                resultado = parsear_trama(linea) if linea is not None else None

                if resultado is None:
                    if time.monotonic() - ultimo_dato > TIMEOUT_SILENCIO:
                        self.error.emit(
                            f"ESP32 en {self._puerto} sin tramas válidas "
                            f"por {TIMEOUT_SILENCIO:.0f} s."
                        )
                        self.desconectado.emit()
                        return
                    continue

                ultimo_dato = time.monotonic()
                temp, sensores = resultado
                self._registrar(linea, temp, sensores)
                self.trigger.emit(temp)
        finally:
            self._cerrar_puerto()

    # ── Helpers internos ───────────────────────────────────────────────────────

    def _registrar(self, linea: str, temp: float, sensores: list[bool]) -> None:
        """
        Guarda una trama válida. Cada sensor se compara con el texto que
        envió en la trama válida anterior: un valor idéntico se marca como
        repetido.
        """
        crudos = [c.strip() for c in linea.split(",")[1:]]
        lecturas = [
            float(c) if presente else None for c, presente in zip(crudos, sensores)
        ]
        repetidos = [
            (c == previo) if presente else None
            for c, previo, presente in zip(crudos, self._crudos_previos, sensores)
        ]
        self._crudos_previos = crudos

        with QMutexLocker(self._mutex):
            self._ultima_temp = temp
            self._estado_sensores = sensores
            self._lecturas = lecturas
            self._repetidos = repetidos
            self._timestamp_lectura = time.monotonic()

    def _abrir_puerto(self) -> bool:
        try:
            self._serial = abrir_serial(self._puerto, TIMEOUT_LINEA_S)
            return True
        except (serial.SerialException, OSError, ValueError) as exc:
            logger.warning("No se pudo abrir %s: %s", self._puerto, exc)
            self.error.emit(
                f"ESP32: no se pudo abrir {self._puerto}. "
                "Revise el puerto en Conexión."
            )
            self.desconectado.emit()
            return False

    def _esperar_primer_dato(self) -> bool:
        deadline = time.monotonic() + ESPERA_PRIMER_S
        while self._activo and time.monotonic() < deadline:
            linea = self._leer_linea()
            if linea is None:
                continue
            resultado = parsear_trama(linea)
            if resultado is None:
                continue

            temp, sensores = resultado
            self._registrar(linea, temp, sensores)
            self.conectado.emit(sensores)

            ausentes = [i + 1 for i, ok in enumerate(sensores) if not ok]
            if ausentes:
                self.error.emit(
                    f"Sensores DS18B20 no detectados: {ausentes}. "
                    "Las mediciones afectadas se guardarán con error_flag=1."
                )
            return True

        if not self._activo:
            return False

        self.error.emit(
            f"ESP32: sin lecturas en {self._puerto} durante {ESPERA_PRIMER_S:.0f} s. "
            "Verifique el cable USB o el puerto en Conexión."
        )
        self.desconectado.emit()
        return False

    def _leer_linea(self) -> str | None:
        try:
            raw = self._serial.readline()
            if raw:
                return raw.decode("utf-8", errors="ignore").strip()
        except serial.SerialException as exc:
            logger.warning("Error de lectura serial en %s: %s", self._puerto, exc)
            self.error.emit(f"ESP32: error de lectura en {self._puerto}.")
        return None

    def _puerto_responde(self, puerto: str) -> bool:
        try:
            with abrir_serial(puerto, TIMEOUT_LINEA_S) as ser:
                limite = time.monotonic() + ESPERA_REINTENTO_S
                while time.monotonic() < limite:
                    raw = ser.readline()
                    if not raw:
                        continue
                    linea = raw.decode("utf-8", errors="ignore").strip()
                    if parsear_trama(linea) is not None:
                        return True
        except (serial.SerialException, OSError):
            return False
        return False

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
