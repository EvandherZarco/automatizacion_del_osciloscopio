"""
config.py
Parámetros de hardware ajustables por el usuario.
Editar antes de ejecutar la aplicación si cambia algún puerto o dirección IP.
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# ── ESP32 WROOM-32 (temperatura) ──────────────────────────────────────────────
TEMP_COM_PORT = "COM5"

# ── Láser EKSPLA NLL455 ───────────────────────────────────────────────────────
LASER_COM_PORT = "COM10"
LASER_DLL_DIR  = str(BASE_DIR / "complementos")

# ── Osciloscopio Tektronix TDS5052B (VXI-11 sobre Ethernet) ──────────────────
OSCIL_HOST = "192.168.1.1"

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
