"""
control_laser.py
Control del láser EKSPLA NLL455 / NL303HT-10-SH vía REMOTECONTROL64.dll.
Toda la comunicación ocurre a través del DLL sobre RS-232.

Registros accesibles según REMOTECONTROL.CSV (col. "Name"):
  State                                    → START / STOP
  Output level                             → E Max / Adjustment / OFF
  Continuous / Burst mode / Trigger burst  → modo de disparo
  Burst length, pulses                     → número de pulsos por ráfaga
  Set cooling temperature                  → temperatura objetivo del agua
  Read cooling temperature                 → temperatura actual del agua (solo lectura)
  Lamp pulse counter                       → contador de disparos (solo lectura)
  Adjustment EO delay                      → retardo EO en modo ajuste (modo seguro = 3800)

Llamado por GUI (modo manual), Medición (modo automático) y Modo Seguro.
"""

from __future__ import annotations

import ctypes
import os
import re
from ctypes import c_int, c_char_p, create_string_buffer, byref
from pathlib import Path

from PySide6.QtCore import QObject, Signal, QMutex, QMutexLocker

from app.config import LASER_COM_PORT, LASER_DLL_DIR

_DLL_NAME = "REMOTECONTROL64.dll"
_CONNECT_RS232 = 1
_DEVICE_BUF = 64
_VALUE_BUF = 256
_READ_TIMEOUT_MS = 2000
_MAX_PING_RETRIES = 3

_REG_STATE      = "State"
_REG_ENERGY     = "Output level"
_REG_BATCH_MODE = "Continuous / Burst mode / Trigger burst"
_REG_BURST_LEN  = "Burst length, pulses"
_REG_READ_COOL  = "Read cooling temperature"
_REG_SET_COOL   = "Set cooling temperature"
_REG_PULSE_CNT  = "Lamp pulse counter"
_REG_EO_ADJ     = "Adjustment EO delay"

_ERR_CODES: dict[int, str] = {
    0: "OK",         1: "NOMOREDATA",    2: "NOCFGFILE",
    3: "WRONGCFGFILE", 4: "BUFFERTOOSHORT", 5: "NOSUCHDEVICE",
    6: "NOSUCHREGISTER", 7: "CANTCONNECT", 8: "TIMEOUT",
    9: "READONLY",   10: "NOT_NV",       11: "HILIMIT",
    12: "LOLIMIT",   13: "NOSUCHVALUE",
}


class LaserController(QObject):

    led_verde    = Signal()
    led_rojo     = Signal()
    led_amarillo = Signal()
    error        = Signal(str)
    cmd_ok       = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dll: ctypes.WinDLL | None = None
        self._device: str = ""
        self._mutex = QMutex()
        self._connected = False
        self._dll_path = Path(LASER_DLL_DIR) / _DLL_NAME

    @property
    def conectado(self) -> bool:
        return self._connected

    # ══════════════════════════════════════════════════════════════════════════
    # CONEXIÓN
    # ══════════════════════════════════════════════════════════════════════════

    def conectar(self) -> bool:
        with QMutexLocker(self._mutex):
            try:
                os.add_dll_directory(str(self._dll_path.parent))
                self._dll = ctypes.WinDLL(str(self._dll_path))
                self._configurar_argtypes()
            except Exception as exc:
                self._emit_fatal(f"No se pudo cargar {_DLL_NAME}: {exc}")
                return False

            com_num = self._com_a_num(LASER_COM_PORT)
            err = self._dll.rcConnect(_CONNECT_RS232, com_num)
            if err != 0:
                self._emit_fatal(
                    f"rcConnect falló (err={err}: {_ERR_CODES.get(err, '?')}). "
                    "Verificar cable RS-232 y que el láser esté encendido."
                )
                return False

            buf = create_string_buffer(_DEVICE_BUF)
            err = self._dll.rcGetFirstDeviceName(buf, len(buf))
            if err != 0:
                self._emit_fatal(
                    f"No se encontraron dispositivos en el bus CAN (err={err})."
                )
                self._dll.rcDisconnect()
                return False

            self._device = buf.value.decode("ascii", errors="replace").strip()
            self._connected = True
            self.led_verde.emit()
            self.cmd_ok.emit(f"Láser conectado — dispositivo: {self._device} ({LASER_COM_PORT})")
            return True

    def desconectar(self) -> None:
        with QMutexLocker(self._mutex):
            if self._dll:
                try:
                    self._dll.rcDisconnect()
                except Exception:
                    pass
            self._connected = False
            self._device = ""
            self.led_rojo.emit()

    # ══════════════════════════════════════════════════════════════════════════
    # COMANDOS DE ESTADO
    # ══════════════════════════════════════════════════════════════════════════

    def start(self) -> bool:
        return self._set_reg(_REG_STATE, "RUN")

    def stop(self) -> bool:
        return self._set_reg(_REG_STATE, "STOP")

    def e_max(self) -> bool:
        return self._set_reg(_REG_ENERGY, "Max")

    def e_adj(self) -> bool:
        return self._set_reg(_REG_ENERGY, "Adjustment")

    def e_off(self) -> bool:
        return self._set_reg(_REG_ENERGY, "OFF")

    def leer_estado(self) -> tuple[bool, str]:
        with QMutexLocker(self._mutex):
            if not self._connected:
                return False, "No conectado"
            return self._get_reg_nl(_REG_STATE)

    # ══════════════════════════════════════════════════════════════════════════
    # BURST
    # ══════════════════════════════════════════════════════════════════════════

    def set_burst_mode(self, modo: str) -> bool:
        """modo: 'Continuous', 'Burst' o 'Trigger'."""
        return self._set_reg(_REG_BATCH_MODE, modo)

    def set_burst_length(self, n_pulsos: int) -> bool:
        return self._set_reg(_REG_BURST_LEN, str(int(n_pulsos)))

    # ══════════════════════════════════════════════════════════════════════════
    # TEMPERATURA DE ENFRIAMIENTO
    # ══════════════════════════════════════════════════════════════════════════

    def set_cooling_temp(self, temp_c: float) -> bool:
        """Establece la temperatura objetivo del agua de enfriamiento (°C)."""
        return self._set_reg(_REG_SET_COOL, f"{temp_c:.1f}")

    def read_cooling_temp(self) -> float | None:
        """Lee la temperatura actual del agua de enfriamiento (°C). Solo lectura."""
        ok, val = self._get_reg(_REG_READ_COOL)
        if not ok:
            return None
        try:
            return float(val.replace("C", "").strip())
        except ValueError:
            return None

    # ══════════════════════════════════════════════════════════════════════════
    # CONTADOR DE PULSOS
    # ══════════════════════════════════════════════════════════════════════════

    def read_pulse_counter(self) -> int | None:
        """Lee el contador total de disparos de la lámpara. Solo lectura."""
        ok, val = self._get_reg(_REG_PULSE_CNT)
        if not ok:
            return None
        try:
            return int(val.strip())
        except ValueError:
            return None

    # ══════════════════════════════════════════════════════════════════════════
    # MODO SEGURO
    # ══════════════════════════════════════════════════════════════════════════

    def modo_seguro(self) -> dict[str, bool]:
        resultados = {
            "stop":          self.stop(),
            "eo_delay_3800": self._set_reg(_REG_EO_ADJ, "3800"),
            "e_off":         self.e_off(),
            "burst_cont":    self.set_burst_mode("Continuous"),
        }
        fallidos = [k for k, v in resultados.items() if not v]
        if fallidos:
            self.error.emit(f"Modo seguro: comandos fallidos → {', '.join(fallidos)}")
            self.led_amarillo.emit()
        else:
            self.cmd_ok.emit("Modo seguro activado — STOP · E OFF · EO delay 3800 · Burst Continuous.")
        return resultados

    # ══════════════════════════════════════════════════════════════════════════
    # HELPERS INTERNOS
    # ══════════════════════════════════════════════════════════════════════════

    def _configurar_argtypes(self) -> None:
        dll = self._dll
        dll.rcConnect.argtypes              = [c_int, c_int]
        dll.rcConnect.restype               = c_int
        dll.rcDisconnect.argtypes           = []
        dll.rcDisconnect.restype            = c_int
        dll.rcGetFirstDeviceName.argtypes   = [c_char_p, c_int]
        dll.rcGetFirstDeviceName.restype    = c_int
        dll.rcGetNextDeviceName.argtypes    = [c_char_p, c_int]
        dll.rcGetNextDeviceName.restype     = c_int
        dll.rcSetRegFromString.argtypes     = [c_char_p, c_char_p, c_char_p]
        dll.rcSetRegFromString.restype      = c_int
        dll.rcGetRegAsString.argtypes       = [
            c_char_p, c_char_p, c_char_p, c_int, c_int, ctypes.POINTER(c_int)
        ]
        dll.rcGetRegAsString.restype        = c_int

    def _set_reg(self, reg: str, value: str) -> bool:
        with QMutexLocker(self._mutex):
            if not self._connected or not self._dll:
                self.error.emit(f"Láser no conectado — ignorado: {reg} = {value}")
                return False

            err = self._dll.rcSetRegFromString(
                self._device.encode("ascii"),
                reg.encode("ascii"),
                value.encode("ascii"),
            )
            if err != 0:
                msg = f"Error escribiendo {reg} = {value} (err={err}: {_ERR_CODES.get(err, '?')})"
                self.error.emit(msg)
                self.led_amarillo.emit()
                return False

            self.cmd_ok.emit(f"Láser: {reg} → {value}")
            return True

    def _get_reg(self, reg: str, timeout_ms: int = _READ_TIMEOUT_MS) -> tuple[bool, str]:
        with QMutexLocker(self._mutex):
            return self._get_reg_nl(reg, timeout_ms)

    def _get_reg_nl(self, reg: str, timeout_ms: int = _READ_TIMEOUT_MS) -> tuple[bool, str]:
        """Lee un registro sin adquirir el mutex (llamar dentro de sección ya bloqueada)."""
        if not self._connected or not self._dll:
            return False, ""

        buf = create_string_buffer(_VALUE_BUF)
        ts  = c_int(0)
        err = self._dll.rcGetRegAsString(
            self._device.encode("ascii"),
            reg.encode("ascii"),
            buf,
            len(buf),
            timeout_ms,
            byref(ts),
        )
        if err != 0:
            return False, ""

        return True, buf.value.decode("ascii", errors="replace").strip()

    def ping(self) -> bool:
        """Verifica que el láser responde. Usado por el monitor de conexión."""
        for _ in range(_MAX_PING_RETRIES):
            ok, val = self._get_reg(_REG_STATE)
            if ok and val:
                return True
        return False

    def _emit_fatal(self, msg: str) -> None:
        self._connected = False
        self.led_rojo.emit()
        self.error.emit(msg)

    @staticmethod
    def _com_a_num(com: str) -> int:
        m = re.search(r"(\d+)", com.upper())
        if not m:
            raise ValueError(f"Puerto COM inválido: {com}")
        return int(m.group(1))