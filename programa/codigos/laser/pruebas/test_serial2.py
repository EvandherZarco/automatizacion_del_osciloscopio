import serial, time

PORT = "COM10"

ser = serial.Serial(
    port     = PORT,
    baudrate = 19200,
    bytesize = serial.EIGHTBITS,
    parity   = serial.PARITY_NONE,
    stopbits = serial.STOPBITS_ONE,
    timeout  = 1.0,
)
ser.reset_input_buffer()

def enviar(cmd: str, receiver: str = "NL") -> str:
    ser.reset_input_buffer()
    frame = f"|[{receiver}:{cmd}\\PC]|".encode("ascii")
    ser.write(frame)
    time.sleep(0.4)
    resp = ser.read(ser.in_waiting or 64)
    decoded = resp.decode("ascii", errors="replace").strip()
    print(f"  TX: {frame}")
    print(f"  RX: {decoded or '(sin respuesta)'}")
    return decoded

print("=== SAY (receiver=NL) ===")
enviar("SAY")

print("=== SAY (sin receiver) ===")
ser.reset_input_buffer()
ser.write(b"|[SAY]|")
time.sleep(0.4)
r = ser.read(ser.in_waiting or 64)
print(f"  RX: {r.decode('ascii', errors='replace').strip()}")

print("=== E1 (receiver=NL) ===")
enviar("E1")

ser.close()