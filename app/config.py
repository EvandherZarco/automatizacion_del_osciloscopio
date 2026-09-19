"""
config.py
Parámetros de hardware por omisión.

Los puertos COM y la IP del osciloscopio se cambian normalmente desde el botón
"Conexión" de la interfaz (se guardan en un JSON en la carpeta del usuario,
ver config_usuario.py). Los valores de este archivo y de config_local.py solo
se usan cuando ese JSON no existe o no es válido.
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# ── ESP32 WROOM-32 (temperatura) ──────────────────────────────────────────────
TEMP_COM_PORT = "COM3"

# ── Láser EKSPLA NLL455 ───────────────────────────────────────────────────────
LASER_COM_PORT = "COM10"
LASER_DLL_DIR = str(BASE_DIR / "complementos")

# ── Osciloscopio Tektronix TDS5054B (VXI-11 sobre Ethernet) ──────────────────
OSCIL_HOST = "192.168.1.100"

# ── Valores locales (no versionados) ──────────────────────────────────────────
# app/config_local.py sobreescribe los parámetros anteriores en cada máquina.
# Si no existe, se conservan los valores definidos arriba.
try:
    from app import config_local as _local
except ImportError:
    _local = None

if _local is not None:
    for _nombre in dir(_local):
        if _nombre.isupper():
            globals()[_nombre] = getattr(_local, _nombre)
    del _nombre
del _local
