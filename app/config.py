"""
config.py
Parámetros de hardware ajustables por el usuario.
Editar antes de ejecutar la aplicación si cambia algún puerto o dirección IP.
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# ── ESP32-C3 (temperatura) ────────────────────────────────────────────────────
TEMP_COM_PORT = "COM3"

# ── Láser EKSPLA NLL455 ───────────────────────────────────────────────────────
LASER_COM_PORT = "COM10"
LASER_DLL_DIR  = str(BASE_DIR / "complementos")

# ── Osciloscopio Tektronix TDS5052B (VXI-11 sobre Ethernet) ──────────────────
OSCIL_HOST = "192.168.1.100"