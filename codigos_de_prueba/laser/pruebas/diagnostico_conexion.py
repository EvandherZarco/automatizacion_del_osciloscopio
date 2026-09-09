"""
diagnostico_conexion.py
Replica paso a paso la ruta de conexión de LaserController.conectar()
sin PySide6 ni GUI, para aislar en qué punto falla el enlace con el láser.

Uso:
    python diagnostico_conexion.py
    python diagnostico_conexion.py COM10
"""

import ctypes
import os
import re
import sys
from ctypes import c_int, c_char_p, create_string_buffer, byref
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(RAIZ))

from app.config import LASER_COM_PORT, LASER_DLL_DIR

DLL_NAME = "REMOTECONTROL64.dll"
CONNECT_RS232 = 1
DEVICE_BUF = 64
VALUE_BUF = 256
READ_TIMEOUT_MS = 2000
REG_STATE = "State"

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


def configurar_argtypes(dll):
    dll.rcConnect.argtypes = [c_int, c_int]
    dll.rcConnect.restype = c_int
    dll.rcDisconnect.argtypes = []
    dll.rcDisconnect.restype = c_int
    dll.rcGetFirstDeviceName.argtypes = [c_char_p, c_int]
    dll.rcGetFirstDeviceName.restype = c_int
    dll.rcGetNextDeviceName.argtypes = [c_char_p, c_int]
    dll.rcGetNextDeviceName.restype = c_int
    dll.rcGetRegAsString.argtypes = [
        c_char_p, c_char_p, c_char_p, c_int, c_int, ctypes.POINTER(c_int)
    ]
    dll.rcGetRegAsString.restype = c_int


def listar_dispositivos(dll):
    encontrados = []

    buf = create_string_buffer(DEVICE_BUF)
    err = dll.rcGetFirstDeviceName(buf, len(buf))
    print(f"[4] rcGetFirstDeviceName -> err={err} ({nombre_err(err)})")
    if err != 0:
        return encontrados

    encontrados.append(buf.value.decode("ascii", errors="replace").strip())
    print(f"    dispositivo 1: {encontrados[0]!r}")

    while True:
        buf = create_string_buffer(DEVICE_BUF)
        err = dll.rcGetNextDeviceName(buf, len(buf))
        if err != 0:
            print(f"    rcGetNextDeviceName -> err={err} ({nombre_err(err)}) — fin de la lista")
            break
        nombre = buf.value.decode("ascii", errors="replace").strip()
        encontrados.append(nombre)
        print(f"    dispositivo {len(encontrados)}: {nombre!r}")

    return encontrados


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
    print(f"[5] rcGetRegAsString('{device}', '{REG_STATE}') -> err={err} ({nombre_err(err)})")
    if err == 0:
        print(f"    valor: {buf.value.decode('ascii', errors='replace').strip()!r}")


def main():
    puerto = sys.argv[1] if len(sys.argv) > 1 else LASER_COM_PORT
    dll_path = Path(LASER_DLL_DIR) / DLL_NAME
    dll_dir = str(dll_path.parent)

    print("=" * 60)
    print("Diagnóstico de conexión con el láser")
    print("=" * 60)
    print(f"Puerto      : {puerto}")
    print(f"DLL         : {dll_path}")
    print(f"DLL existe  : {dll_path.is_file()}")
    print(f"CWD inicial : {os.getcwd()}")
    print("-" * 60)

    if not dll_path.is_file():
        print("El DLL no existe en esa ruta. Revisar complementos/.")
        return 1

    for auxiliar in ("CANRS232.INI", "REMOTECONTROL.CSV"):
        ruta = Path(dll_dir) / auxiliar
        print(f"{auxiliar:<20}: {'presente' if ruta.is_file() else 'AUSENTE'}")
    print("-" * 60)

    dll = None
    prev_dir = os.getcwd()
    conectado = False

    try:
        os.add_dll_directory(dll_dir)
        os.chdir(dll_dir)
        print(f"[1] chdir a {os.getcwd()}")

        dll = ctypes.WinDLL(str(dll_path))
        print(f"[2] WinDLL cargado: {dll._name}")

        configurar_argtypes(dll)

        num = com_a_num(puerto)
        err = dll.rcConnect(CONNECT_RS232, num)
        print(f"[3] rcConnect({CONNECT_RS232}, {num}) -> err={err} ({nombre_err(err)})")
        conectado = err == 0
    except Exception as exc:
        print(f"EXCEPCIÓN durante la carga o el rcConnect: {type(exc).__name__}: {exc}")
        os.chdir(prev_dir)
        return 1
    finally:
        os.chdir(prev_dir)

    print(f"    CWD restaurado: {os.getcwd()}")
    print("-" * 60)

    if not conectado:
        print("rcConnect falló — el DLL no tomó el puerto.")
        print("Códigos frecuentes: 7=CANTCONNECT (puerto ocupado o inexistente),")
        print("2=NOCFGFILE / 3=WRONGCFGFILE (CANRS232.INI no encontrado o inválido).")
        return 1

    codigo = 0
    try:
        dispositivos = listar_dispositivos(dll)
        print("-" * 60)
        if not dispositivos:
            print("El puerto abrió, pero NO hay dispositivos en el bus CAN.")
            print("Esto apunta al láser: apagado, en standby, o su controlador")
            print("no está enumerando. El estado del puerto COM no lo descarta.")
            codigo = 1
        else:
            print(f"Dispositivos enumerados: {len(dispositivos)}")
            leer_estado(dll, dispositivos[0])
            print("-" * 60)
            print("El enlace con el láser funciona fuera de la GUI.")
    finally:
        err = dll.rcDisconnect()
        print("-" * 60)
        print(f"[6] rcDisconnect -> err={err} ({nombre_err(err)})")

    return codigo


if __name__ == "__main__":
    sys.exit(main())
