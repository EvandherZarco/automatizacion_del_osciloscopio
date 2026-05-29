import vxi11
import numpy as np

# 1. Conexión
scope = vxi11.Instrument("192.168.1.100")
print("Conectado:", scope.ask("*IDN?"))

# 2. Configurar fuente y formato
scope.write("DATA:SOURCE CH1")
scope.write("DATA:ENC RIBINARY")
scope.write("DATA:WIDTH 1")

# 3. Parámetros de escala
xincr  = float(scope.ask("WFMPRE:XINCR?"))
ymult  = float(scope.ask("WFMPRE:YMULT?"))
yoff   = float(scope.ask("WFMPRE:YOFF?"))
yzero  = float(scope.ask("WFMPRE:YZERO?"))
xzero  = float(scope.ask("WFMPRE:XZERO?"))
pt_off = int(scope.ask("WFMPRE:PT_OFF?"))

print(f"Escala tiempo : {xincr*1e9:.3f} ns/punto")
print(f"Escala voltaje: {ymult*1000:.3f} mV/count")

# 4. Captura — b"CURVE?" en bytes es crítico
raw = scope.ask_raw(b"CURVE?")
n_digits  = int(chr(raw[1]))
data      = np.frombuffer(raw[2 + n_digits:], dtype=np.int8)

# 5. Conversión
voltaje = (data.astype(float) - yoff) * ymult + yzero
tiempo  = xzero + (np.arange(len(data)) - pt_off) * xincr

print(f"Muestras capturadas: {len(data)}")
print(f"Voltaje mín: {voltaje.min():.4f} V")
print(f"Voltaje máx: {voltaje.max():.4f} V")
print(f"Rango tiempo: {tiempo[0]*1e6:.3f} µs → {tiempo[-1]*1e6:.3f} µs")