"""
prueba_conexion.py
Sondeos de conexión usados por el diálogo "Conexión" de la GUI.

Cada función prueba un dispositivo con el puerto o dirección que recibe,
sin tocar la configuración guardada ni los controladores de la aplicación,
y libera todo lo que abre antes de retornar. Devuelven (ok, mensaje) con un
texto listo para mostrar al usuario, sin trazas de excepción.

El láser se prueba directamente sobre REMOTECONTROL64.dll porque el estado
de esa biblioteca es global al proceso: la prueba termina siempre con
rcDisconnect para no bloquear la siguiente conexión real.
"""

from __future__ import annotations

import ctypes
import logging
import os
import re
import time
from ctypes import c_int, c_char_p, create_string_buffer
from pathlib import Path

import serial
import vxi11

from app import config
from app.laser.control_laser import _ERR_CODES
from app.temperatura.temperatura import BAUD_RATE, parsear_trama

logger = logging.getLogger(__name__)

ESPERA_ESP32_S = 4.0
TIMEOUT_OSCIL_S = 5.0

_DLL_NAME = "REMOTECONTROL64.dll"
_CONNECT_RS232 = 1
_DEVICE_BUF = 64


def probar_esp32(puerto: str) -> tuple[bool, str]:
    """Abre el puerto, espera una trama válida y lo cierra."""
    try:
        ser = serial.Serial(port=puerto, baudrate=BAUD_RATE, timeout=1.0)
    except (serial.SerialException, OSError, ValueError) as exc:
        logger.warning("ESP32: no se pudo abrir %s: %s", puerto, exc)
        return False, f"ESP32: no se pudo abrir {puerto} — {_motivo_serial(exc)}."

    try:
        limite = time.monotonic() + ESPERA_ESP32_S
        while time.monotonic() < limite:
            try:
                raw = ser.readline()
            except (serial.SerialException, OSError) as exc:
                logger.warning("ESP32: error de lectura en %s: %s", puerto, exc)
                return False, f"ESP32: error de lectura en {puerto}."
            if not raw:
                continue
            linea = raw.decode("utf-8", errors="ignore").strip()
            resultado = parsear_trama(linea)
            if resultado is not None:
                temp, sensores = resultado
                presentes = sum(sensores)
                return True, (
                    f"ESP32: responde en {puerto} "
                    f"({temp:.2f} °C, {presentes} de 4 sensores presentes)."
                )
        return False, (
            f"ESP32: {puerto} se abrió pero no envió lecturas en "
            f"{ESPERA_ESP32_S:.0f} s. ¿Es el puerto correcto?"
        )
    finally:
        try:
            ser.close()
        except (serial.SerialException, OSError):
            pass


def probar_osciloscopio(host: str) -> tuple[bool, str]:
    """Abre un enlace VXI-11, pide *IDN? y cierra el enlace."""
    inst = None
    try:
        inst = vxi11.Instrument(host)
        inst.timeout = TIMEOUT_OSCIL_S
        idn = inst.ask("*IDN?").strip()
    except Exception as exc:
        logger.warning("Osciloscopio: sin respuesta en %s: %s", host, exc)
        return False, (
            f"Osciloscopio: sin respuesta en {host}. Verifique la IP, "
            "el cable Ethernet y que la app TekScope esté corriendo."
        )
    finally:
        if inst is not None:
            try:
                inst.close()
            except Exception:
                pass

    partes = idn.split(",")
    modelo = partes[1].strip() if len(partes) > 1 else idn
    if not modelo.startswith("TDS"):
        return False, f"Osciloscopio: en {host} responde otro instrumento ({idn})."
    return True, f"Osciloscopio: {modelo} responde en {host}."


def probar_laser(puerto: str) -> tuple[bool, str]:
    """Conecta por RS-232, lee el nombre del primer dispositivo y desconecta."""
    m = re.search(r"(\d+)", puerto.upper())
    if not m:
        return False, f"Láser: «{puerto}» no es un puerto COM válido."
    com_num = int(m.group(1))

    dll_path = Path(config.LASER_DLL_DIR) / _DLL_NAME
    prev_dir = os.getcwd()
    try:
        os.add_dll_directory(str(dll_path.parent))
        os.chdir(str(dll_path.parent))
        try:
            dll = ctypes.WinDLL(str(dll_path))
            dll.rcConnect.argtypes = [c_int, c_int]
            dll.rcConnect.restype = c_int
            dll.rcDisconnect.argtypes = []
            dll.rcDisconnect.restype = c_int
            dll.rcGetFirstDeviceName.argtypes = [c_char_p, c_int]
            dll.rcGetFirstDeviceName.restype = c_int
        except Exception as exc:
            logger.warning("Láser: no se pudo cargar %s: %s", _DLL_NAME, exc)
            return False, f"Láser: no se pudo cargar la biblioteca {_DLL_NAME}."

        try:
            err = dll.rcConnect(_CONNECT_RS232, com_num)
        except Exception as exc:
            logger.warning("Láser: fallo inesperado en rcConnect %s: %s", puerto, exc)
            return False, f"Láser: fallo al comunicar por {puerto}."
    finally:
        os.chdir(prev_dir)

    if err != 0:
        nombre_err = _ERR_CODES.get(err, "desconocido")
        logger.warning("Láser: rcConnect en %s devolvió %d (%s)", puerto, err, nombre_err)
        return False, (
            f"Láser: sin respuesta en {puerto} (error {err}={nombre_err}). "
            "Verifique el cable RS-232 y que el láser esté encendido."
        )

    try:
        buf = create_string_buffer(_DEVICE_BUF)
        err = dll.rcGetFirstDeviceName(buf, len(buf))
        if err != 0:
            logger.warning("Láser: rcGetFirstDeviceName devolvió %d", err)
            return False, (
                f"Láser: {puerto} abre, pero no se encontró ningún dispositivo "
                "en el bus. Verifique que el láser esté encendido."
            )
        nombre = buf.value.decode("ascii", errors="replace").strip()
        return True, f"Láser: {nombre or 'dispositivo'} responde en {puerto}."
    except Exception as exc:
        logger.warning("Láser: fallo inesperado en %s: %s", puerto, exc)
        return False, f"Láser: fallo al comunicar por {puerto}."
    finally:
        try:
            dll.rcDisconnect()
        except Exception:
            pass


def _motivo_serial(exc: Exception) -> str:
    texto = str(exc)
    if "PermissionError" in texto or "Acceso denegado" in texto or "denied" in texto:
        return "el puerto está en uso por otro programa"
    if "FileNotFoundError" in texto or "no existe" in texto or "cannot find" in texto:
        return "el puerto no existe en este equipo"
    return "no fue posible abrirlo"
