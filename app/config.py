"""
config.py
Parámetros de hardware ajustables por el usuario.
Editar antes de ejecutar la aplicación si cambia algún puerto o dirección IP.
"""

# ── ESP32-C3 (temperatura) ────────────────────────────────────────────────────
TEMP_COM_PORT = "COM3"

# ── Láser EKSPLA NLL455 ───────────────────────────────────────────────────────
LASER_COM_PORT = "COM10"
LASER_DLL_DIR  = r"C:\Users\i7\Desktop\automatizacion_Zarco\complementos"

# ── Osciloscopio Tektronix TDS5052B (VXI-11 sobre Ethernet) ──────────────────
OSCIL_HOST = "192.168.1.100"