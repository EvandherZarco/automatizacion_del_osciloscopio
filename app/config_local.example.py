"""
config_local.example.py
Copiar este archivo como app/config_local.py y ajustar los valores a la máquina
donde corre la aplicación. config_local.py no se versiona.
"""

# ── ESP32 WROOM-32 (temperatura) ──────────────────────────────────────────────
TEMP_COM_PORT = "COM5"

# ── Láser EKSPLA NLL455 ───────────────────────────────────────────────────────
LASER_COM_PORT = "COM10"

# ── Osciloscopio Tektronix TDS5052B (VXI-11 sobre Ethernet) ──────────────────
OSCIL_HOST = "192.168.1.1"
