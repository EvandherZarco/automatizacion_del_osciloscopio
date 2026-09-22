"""
monitor_state.py
Lee el registro State del láser cada 0.5 s e imprime la hora con milisegundos,
el valor leído, el timestamp que devuelve el DLL y cuánto tardó la lectura.
Sirve para ver la secuencia de estados entre que se presiona RUN en el control
CAN y que el láser empieza a emitir.

Solo lectura: no escribe ningún registro. Cerrar la app antes de ejecutarlo,
porque el puerto COM no puede estar abierto por dos procesos.

Uso:
    python monitor_state.py
    python monitor_state.py COM10

Ctrl+C para terminar.
"""

import ctypes
import os
import re
import sys
import time
from ctypes import c_int, c_char_p, create_string_buffer, byref
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(RAIZ))

from app import config_usuario
from app.config import LASER_DLL_DIR

DLL_NAME = "REMOTECONTROL64.dll"
CONNECT_RS232 = 1
DEVICE_BUF = 64
VALUE_BUF = 256
READ_TIMEOUT_MS = 2000
REG_STATE = "State"
PERIODO_S = 0.5

ERR_CODES = {
    0: "OK",           1: "NOMOREDATA",     2: "NOCFGFILE",
    3: "WRONGCFGFILE", 4: "BUFFERTOOSHORT", 5: "NOSUCHDEVICE",
    6: "NOSUCHREGISTER", 7: "CANTCONNECT",  8: "TIMEOUT",
    9: "READONLY",     10: "NOT_NV",        11: "HILIMIT",
    12: "LOLIMIT",     13: "NOSUCHVALUE",
}


def nombre_err(err):
    return ERR_CODES.get(err, "?")


def com_a_num(com):
    m = re.search(r"(\d+)", com.upper())
    if not m:
        raise ValueError(f"Puerto COM inválido: {com}")
    return int(m.group(1))


def hora():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def configurar_argtypes(dll):
    dll.rcConnect.argtypes = [c_int, c_int]
    dll.rcConnect.restype = c_int
    dll.rcDisconnect.argtypes = []
    dll.rcDisconnect.restype = c_int
    dll.rcGetFirstDeviceName.argtypes = [c_char_p, c_int]
    dll.rcGetFirstDeviceName.restype = c_int
    dll.rcGetRegAsString.argtypes = [
        c_char_p, c_char_p, c_char_p, c_int, c_int, ctypes.POINTER(c_int)
    ]
    dll.rcGetRegAsString.restype = c_int


def conectar(dll_path, puerto):
    dll_dir = str(dll_path.parent)
    prev_dir = os.getcwd()
    try:
        os.add_dll_directory(dll_dir)
        os.chdir(dll_dir)
        dll = ctypes.WinDLL(str(dll_path))
        configurar_argtypes(dll)
        err = dll.rcConnect(CONNECT_RS232, com_a_num(puerto))
    finally:
        os.chdir(prev_dir)
    if err != 0:
        print(f"rcConnect falló: err={err} ({nombre_err(err)})")
        return None
    return dll


def leer_estado(dll, device):
    buf = create_string_buffer(VALUE_BUF)
    ts = c_int(0)
    err = dll.rcGetRegAsString(
        device.encode("ascii"),
        REG_STATE.encode("ascii"),
        buf,
        len(buf),
        READ_TIMEOUT_MS,
        byref(ts),
    )
    valor = buf.value.decode("ascii", errors="replace").strip() if err == 0 else None
    return err, valor, ts.value


def main():
    puerto = sys.argv[1] if len(sys.argv) > 1 else config_usuario.obtener("LASER_COM_PORT")
    dll_path = Path(LASER_DLL_DIR) / DLL_NAME

    print(f"Puerto: {puerto}   DLL: {dll_path}")
    dll = conectar(dll_path, puerto)
    if dll is None:
        return 1

    try:
        buf = create_string_buffer(DEVICE_BUF)
        err = dll.rcGetFirstDeviceName(buf, len(buf))
        if err != 0:
            print(f"Sin dispositivos en el bus CAN: err={err} ({nombre_err(err)})")
            return 1
        device = buf.value.decode("ascii", errors="replace").strip()
        print(f"Dispositivo: {device!r}   periodo: {PERIODO_S} s   Ctrl+C para terminar")
        print("-" * 64)
        print(f"{'hora':<12}  {'State':<8}  {'ts DLL':>10}  {'lectura':>8}")
        print("-" * 64)

        anterior = object()
        siguiente = time.perf_counter()
        while True:
            inicio = hora()
            t0 = time.perf_counter()
            err, valor, ts = leer_estado(dll, device)
            dur_ms = (time.perf_counter() - t0) * 1000
            if err == 0:
                texto = valor
            else:
                texto = f"ERR {err} ({nombre_err(err)})"
            marca = "  <-- cambio" if texto != anterior else ""
            anterior = texto
            print(f"{inicio:<12}  {texto:<8}  {ts:>10}  {dur_ms:>6.0f} ms{marca}", flush=True)

            siguiente += PERIODO_S
            espera = siguiente - time.perf_counter()
            if espera > 0:
                time.sleep(espera)
            else:
                siguiente = time.perf_counter()
    except KeyboardInterrupt:
        print("-" * 64)
        print("Interrumpido por el usuario.")
    finally:
        err = dll.rcDisconnect()
        print(f"rcDisconnect -> err={err} ({nombre_err(err)})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
