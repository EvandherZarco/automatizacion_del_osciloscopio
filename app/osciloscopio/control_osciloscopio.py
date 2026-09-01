"""
control_osciloscopio.py
Control del osciloscopio Tektronix TDS5052B via VXI-11 sobre Ethernet.
Protocolo verificado en hardware con python-vxi11.
Prerequisito: app interna del osciloscopio corriendo en Windows XP.

Modos de operación:
  Manual           — NUMAVG=1, captura única sin promediado.
  Modo tiempo      — el scope promedia NUMAVG_TIEMPO disparos internamente
                     y para solo; Python hace polling de ACQ:STATE? hasta 0.
  Modo temperatura — Python controla la ventana con acq_run() / acq_stop_and_capture().
                     NUMAVG se pone en 10000 para que el scope nunca pare solo.
"""

from __future__ import annotations

import math
import time
import numpy as np
import vxi11
from dataclasses import dataclass
from PySide6.QtCore import QObject, Signal, QMutex, QMutexLocker

from app.config import OSCIL_HOST

TIMEOUT_S = 30.0
MAX_REINTENTOS = 3
POLL_INTERVAL_S = 0.5
POLL_TIMEOUT_S = 90.0

NUMAVG_TIEMPO = 100
NUMAVG_TEMPERATURA = 10000

FREC_DISPARO_HZ = 10.0
TRANSFER_BYTES_POR_S = 250_000.0
OVERHEAD_TRANSFERENCIA_S = 1.0

_CANALES_VALIDOS = ("CH1", "CH2")
_CANAL_DEFAULT = "CH1"


@dataclass
class CapturaOscil:
    raw_data: np.ndarray
    wfmpre: dict
    voltaje: np.ndarray
    tiempo: np.ndarray
    error_flag: int = 0


class OsciloscopioController(QObject):

    led_verde = Signal()
    led_rojo = Signal()
    led_amarillo = Signal()
    error = Signal(str)
    cmd_ok = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._inst: vxi11.Instrument | None = None
        self._mutex = QMutex()
        self._connected = False
        self._idn: str = ""
        self._modelo: str = ""
        self._canal: str = _CANAL_DEFAULT
        self._nr_pt: int = 0
        self._cancelar_espera = False

    @property
    def conectado(self) -> bool:
        return self._connected

    @property
    def canal(self) -> str:
        return self._canal

    @property
    def idn(self) -> str:
        """Cadena *IDN? completa del instrumento conectado."""
        return self._idn

    @property
    def modelo(self) -> str:
        """Modelo extraído del *IDN?, por ejemplo 'TDS5054B'."""
        return self._modelo

    # ── Conexión ──────────────────────────────────────────────────────────────

    def conectar(self) -> bool:
        with QMutexLocker(self._mutex):
            try:
                inst = vxi11.Instrument(OSCIL_HOST)
                inst.timeout = TIMEOUT_S
            except Exception as exc:
                self._emit_conn_error(f"No se pudo crear instrumento VXI-11: {exc}")
                return False

            idn = self._ping(inst)
            if idn is None:
                self._emit_conn_error(
                    "Osciloscopio no responde a *IDN?. "
                    f"Verificar: app Windows XP corriendo, cable Ethernet, IP {OSCIL_HOST}."
                )
                return False

            try:
                modelo = idn.split(",")[1].strip()
            except IndexError:
                self._emit_conn_error(f"IDN con formato inesperado: {idn.strip()}")
                return False

            if not modelo.startswith("TDS"):
                self._emit_conn_error(f"Instrumento desconocido: {idn.strip()}")
                return False

            nr_pt = self._configurar_base(inst)
            if nr_pt is None:
                self._emit_conn_error(
                    "Error en la configuración base del osciloscopio."
                )
                return False

            self._inst = inst
            self._idn = idn.strip()
            self._modelo = modelo
            self._nr_pt = nr_pt
            self._connected = True
            self.led_verde.emit()
            self.cmd_ok.emit(
                f"Osciloscopio conectado — {modelo} ({OSCIL_HOST}). "
                f"Puntos de waveform: {nr_pt}."
            )
            return True

    def desconectar(self) -> None:
        with QMutexLocker(self._mutex):
            if self._inst:
                try:
                    self._inst.close()
                except Exception:
                    pass
                self._inst = None
            self._connected = False
            self.led_rojo.emit()

    def ping(self) -> bool:
        with QMutexLocker(self._mutex):
            if not self._inst:
                return False
            return self._ping(self._inst) is not None

    def cancelar_espera(self) -> None:
        self._cancelar_espera = True

    def leer_escala_actual(self) -> dict | None:
        """
        Lee del hardware los parámetros de escala actuales.
        Retorna dict con claves: vdiv_v, tdiv_s, coupling, trigger_v, acq_mode, numavg.
        Retorna None si el osciloscopio no responde.
        """
        with QMutexLocker(self._mutex):
            if not self._inst:
                return None
            try:
                vdiv = float(self._inst.ask(f"{self._canal}:SCALE?").strip())
                tdiv = float(self._inst.ask("HORizontal:SCAle?").strip())
                coup = self._inst.ask(f"{self._canal}:COUPling?").strip().upper()
                trig = float(self._inst.ask("TRIGger:MAIn:LEVel?").strip())
                mode = self._inst.ask("ACQ:MODE?").strip().upper()
                numavg = int(self._inst.ask("ACQ:NUMAVG?").strip())
                return {
                    "vdiv_v": vdiv,
                    "tdiv_s": tdiv,
                    "coupling": coup,
                    "trigger_v": trig,
                    "acq_mode": mode,
                    "numavg": numavg,
                }
            except Exception as exc:
                self.error.emit(f"Error al leer escala actual: {exc}")
                return None

    def set_acq_mode(self, modo: str) -> bool:
        """
        Establece el modo de adquisición.
        modo: 'SAMPLE' | 'AVERAGE'
        """
        modo = modo.upper()
        if modo not in ("SAMPLE", "AVERAGE"):
            self.error.emit(f"Modo de adquisición inválido: {modo}")
            return False
        with QMutexLocker(self._mutex):
            if not self._inst:
                return False
            try:
                self._inst.write(f"ACQ:MODE {modo}")
                self.cmd_ok.emit(f"Osciloscopio: adquisición → {modo}")
                return True
            except Exception as exc:
                self.error.emit(f"Error al configurar modo ACQ: {exc}")
                return False

    def set_numavg(self, n: int) -> bool:
        """Establece el número de promedios. Solo tiene efecto si ACQ:MODE = AVERAGE."""
        with QMutexLocker(self._mutex):
            if not self._inst:
                return False
            return self._set_numavg(self._inst, n)

    # ── Canal de señal ────────────────────────────────────────────────────────

    def set_canal(self, canal: str) -> bool:
        """
        Cambia el canal activo (CH1 o CH2) y lo aplica al osciloscopio.
        Solo tiene efecto cuando no hay una adquisición en curso.
        """
        canal = canal.upper()
        if canal not in _CANALES_VALIDOS:
            self.error.emit(f"Canal inválido: {canal}. Opciones: {_CANALES_VALIDOS}")
            return False

        with QMutexLocker(self._mutex):
            if not self._inst:
                self._canal = canal
                return True
            try:
                self._inst.write(f"DATA:SOURCE {canal}")
                self._canal = canal
                self.cmd_ok.emit(f"Canal activo: {canal}")
                return True
            except Exception as exc:
                self.error.emit(f"Error al cambiar canal a {canal}: {exc}")
                return False

    # ── Configuración de modo ─────────────────────────────────────────────────

    def configurar_modo_tiempo(self, numavg: int = NUMAVG_TIEMPO) -> bool:
        with QMutexLocker(self._mutex):
            if not self._inst:
                return False
            try:
                self._inst.write("ACQ:MODE AVERAGE")
                self._inst.write("ACQ:STOPAFTER SEQUENCE")
            except Exception as exc:
                self._emit_conn_error(f"Error al configurar modo tiempo: {exc}")
                return False
            return self._set_numavg(self._inst, numavg)

    def configurar_modo_temperatura(self) -> bool:
        with QMutexLocker(self._mutex):
            if not self._inst:
                return False
            try:
                self._inst.write("ACQ:MODE AVERAGE")
                self._inst.write("ACQ:STOPAFTER SEQUENCE")
            except Exception as exc:
                self._emit_conn_error(f"Error al configurar modo temperatura: {exc}")
                return False
            return self._set_numavg(self._inst, NUMAVG_TEMPERATURA)

    # ── Lectura directa de pantalla ───────────────────────────────────────────

    def leer_pantalla(self) -> CapturaOscil | None:
        """
        Captura rápida que respeta la configuración actual del panel frontal.
        Pone el scope en modo libre (RUNSTOP), adquiere brevemente y lee.
        """
        with QMutexLocker(self._mutex):
            if not self._inst:
                return None
            try:
                self._inst.write("ACQ:STOPAFTER RUNSTOP")
                self._inst.write("ACQ:STATE RUN")
                time.sleep(0.5)
                self._inst.write("ACQ:STATE STOP")
                self._inst.ask("*OPC?")
            except Exception as exc:
                self._emit_conn_error(f"Error en lectura de pantalla: {exc}")
                return None
            return self._leer_waveform(self._inst)

    # ── Captura modo manual ───────────────────────────────────────────────────

    def capturar(self) -> CapturaOscil | None:
        """
        Captura única para modo manual de la GUI (NUMAVG=1, sin promediado).
        """
        with QMutexLocker(self._mutex):
            if not self._inst:
                return None

            if not self._set_numavg(self._inst, 1):
                return None

            try:
                self._inst.write("ACQ:MODE AVERAGE")
                self._inst.write("ACQ:STOPAFTER SEQUENCE")
                self._inst.write("ACQ:STATE RUN")
            except Exception as exc:
                self._emit_conn_error(f"Error al iniciar adquisición manual: {exc}")
                return None

            if not self._esperar_fin(self._inst):
                self.error.emit(
                    f"Timeout: el osciloscopio no terminó la adquisición en {POLL_TIMEOUT_S:.0f} s."
                )
                self.led_amarillo.emit()
                captura = self._leer_waveform(self._inst)
                if captura:
                    captura.error_flag = 1
                return captura

            return self._leer_waveform(self._inst)

    # ── Captura modo tiempo ───────────────────────────────────────────────────

    def estimar_tiempo_captura(self, numavg: int = NUMAVG_TIEMPO) -> float:
        """
        Tiempo aproximado de una captura en modo tiempo, en segundos.
        Suma el promediado (numavg disparos a la frecuencia del laser,
        cuantizado por el periodo de polling) y la transferencia de la
        forma de onda por VXI-11.
        """
        n = max(1, int(numavg))
        t_promedio = n / FREC_DISPARO_HZ
        t_promedio = math.ceil(t_promedio / POLL_INTERVAL_S) * POLL_INTERVAL_S
        puntos = self._nr_pt if self._nr_pt > 0 else 0
        t_transfer = (puntos * 2) / TRANSFER_BYTES_POR_S
        return t_promedio + t_transfer + OVERHEAD_TRANSFERENCIA_S

    def capturar_modo_tiempo(self) -> CapturaOscil | None:
        """
        Captura con promediado para modo tiempo. Requiere que NUMAVG < NUMAVG_TEMPERATURA;
        si el scope está configurado para modo temperatura este método no debe llamarse.
        """
        with QMutexLocker(self._mutex):
            if not self._inst:
                return None

            numavg_actual = self._leer_numavg_actual(self._inst)
            if numavg_actual is not None and numavg_actual >= NUMAVG_TEMPERATURA:
                self.error.emit(
                    f"capturar_modo_tiempo llamado con NUMAVG={numavg_actual}. "
                    "Usa acq_run / acq_stop_and_capture para modo temperatura."
                )
                return None

            try:
                self._inst.write("ACQ:STOPAFTER SEQUENCE")
                self._inst.write("ACQ:STATE RUN")
            except Exception as exc:
                self._emit_conn_error(f"Error al iniciar adquisición: {exc}")
                return None

            if not self._esperar_fin(self._inst):
                self.error.emit(
                    f"Timeout: el osciloscopio no terminó el promedio en {POLL_TIMEOUT_S:.0f} s."
                )
                self.led_amarillo.emit()
                captura = self._leer_waveform(self._inst)
                if captura:
                    captura.error_flag = 1
                self._reanudar_freerun(self._inst)
                return captura

            captura = self._leer_waveform(self._inst)
            self._reanudar_freerun(self._inst)
            return captura

    # ── Captura modo temperatura ──────────────────────────────────────────────

    def acq_run(self) -> bool:
        with QMutexLocker(self._mutex):
            if not self._inst:
                return False
            try:
                self._inst.write("ACQ:STATE RUN")
                return True
            except Exception as exc:
                self._emit_conn_error(f"Error al iniciar ACQ:STATE RUN: {exc}")
                return False

    def acq_stop_and_capture(self) -> CapturaOscil | None:
        with QMutexLocker(self._mutex):
            if not self._inst:
                return None
            try:
                self._inst.write("ACQ:STATE STOP")
                self._inst.ask("*OPC?")
            except Exception as exc:
                self._emit_conn_error(f"Error al detener adquisición: {exc}")
                return None
            return self._leer_waveform(self._inst)

    # ── Helpers internos ──────────────────────────────────────────────────────

    def _ping(self, inst: vxi11.Instrument) -> str | None:
        try:
            return inst.ask("*IDN?")
        except Exception:
            return None

    def _configurar_base(self, inst: vxi11.Instrument) -> int | None:
        """
        Envía configuración base de transferencia de datos.
        Solo configura el formato de transferencia, no toca parámetros
        de adquisición (modo, promediado, stopafter) para no alterar
        lo que el usuario configuró en el panel frontal.
        """
        cmds_pre = [
            f"DATA:SOURCE {self._canal}",
            "DATA:ENCDG RIBINARY",
            "DATA:WIDTH 2",
            "DATA:START 1",
            "DATA:STOP 1000000",
        ]
        try:
            for cmd in cmds_pre:
                inst.write(cmd)
            nr_pt = int(inst.ask("WFMPRE:NR_PT?").strip())
            return nr_pt
        except Exception as exc:
            self._emit_conn_error(f"Error en configuración base: {exc}")
            return None

    def _set_numavg(self, inst: vxi11.Instrument, n: int) -> bool:
        try:
            inst.write(f"ACQ:NUMAVG {n}")
            return True
        except Exception as exc:
            self._emit_conn_error(f"Error al configurar ACQ:NUMAVG {n}: {exc}")
            return False

    def _leer_numavg_actual(self, inst: vxi11.Instrument) -> int | None:
        try:
            return int(inst.ask("ACQ:NUMAVG?").strip())
        except Exception:
            return None

    def _esperar_fin(self, inst: vxi11.Instrument) -> bool:
        self._cancelar_espera = False
        deadline = time.monotonic() + POLL_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._cancelar_espera:
                return False
            try:
                state = inst.ask("ACQ:STATE?").strip()
                if state == "0":
                    return True
            except Exception as exc:
                self._emit_conn_error(f"Error al consultar ACQ:STATE?: {exc}")
                return False
            time.sleep(POLL_INTERVAL_S)
        return False

    def _leer_waveform(self, inst: vxi11.Instrument) -> CapturaOscil | None:
        wfmpre = self._leer_wfmpre(inst)
        if wfmpre is None:
            return None
        raw = self._leer_curve(inst)
        if raw is None:
            return None
        voltaje, tiempo = self._convertir(raw, wfmpre)
        return CapturaOscil(raw_data=raw, wfmpre=wfmpre, voltaje=voltaje, tiempo=tiempo)

    def _leer_wfmpre(self, inst: vxi11.Instrument) -> dict | None:
        params = ["XINCR", "XZERO", "PT_OFF", "YMULT", "YOFF", "YZERO", "NR_PT"]
        for intento in range(MAX_REINTENTOS):
            try:
                wfmpre = {}
                for p in params:
                    raw = inst.ask(f"WFMPRE:{p}?").strip()
                    wfmpre[p] = int(raw) if p in ("PT_OFF", "NR_PT") else float(raw)
                wfmpre["CH_SCALE"] = float(inst.ask(f"{self._canal}:SCALE?").strip())
                wfmpre["HOR_SCALE"] = float(inst.ask("HORizontal:SCAle?").strip())
                return wfmpre
            except Exception as exc:
                if intento == MAX_REINTENTOS - 1:
                    self._emit_capture_warning(
                        f"No se pudieron leer parámetros WFMPRE: {exc}"
                    )
        return None

    def _leer_curve(self, inst: vxi11.Instrument) -> np.ndarray | None:
        for intento in range(MAX_REINTENTOS):
            try:
                inst.write("CURVE?")
                raw_bytes = inst.read_raw()
                resultado = self._parsear_curve(raw_bytes)
                if resultado is not None:
                    return resultado
            except Exception as exc:
                if intento == MAX_REINTENTOS - 1:
                    self._emit_capture_warning(f"Error al leer CURVE: {exc}")
        return None

    def _parsear_curve(self, raw_bytes: bytes) -> np.ndarray | None:
        if len(raw_bytes) < 2 or chr(raw_bytes[0]) != "#":
            self._emit_capture_warning("Respuesta CURVE con formato inesperado.")
            return None

        n_digits = int(chr(raw_bytes[1]))
        data_start = 2 + n_digits
        data = raw_bytes[data_start:]

        if data and data[-1] == ord("\n"):
            data = data[:-1]

        arr = np.frombuffer(data, dtype=np.dtype(">i2")).copy()

        if len(arr) == 0:
            self._emit_capture_warning("CURVE devolvió un array vacío.")
            return None

        return arr

    def _convertir(
        self, raw: np.ndarray, wfmpre: dict
    ) -> tuple[np.ndarray, np.ndarray]:
        n = len(raw)
        voltaje = (raw.astype(np.float64) - wfmpre["YOFF"]) * wfmpre["YMULT"] + wfmpre["YZERO"]
        tiempo = (np.arange(n) - wfmpre["PT_OFF"]) * wfmpre["XINCR"] + wfmpre["XZERO"]
        return voltaje, tiempo

    def _emit_conn_error(self, msg: str) -> None:
        """Error de conexión o comunicación — desconecta el instrumento lógicamente."""
        self._connected = False
        self.led_rojo.emit()
        self.error.emit(msg)

    def _reanudar_freerun(self, inst: vxi11.Instrument) -> None:
        try:
            inst.write("ACQ:STOPAFTER RUNSTOP")
            inst.write("ACQ:STATE RUN")
        except Exception:
            pass

    def _emit_capture_warning(self, msg: str) -> None:
        """Error de captura de waveform — la conexión VXI-11 sigue viva."""
        self.led_amarillo.emit()
        self.error.emit(msg)
