import serial, time

PORT     = "COM10"
SEGUNDOS = 3  # cambiá esto si querés más o menos tiempo

def enviar(ser, cmd: str) -> str:
    ser.reset_input_buffer()
    frame = f"|[NL:{cmd}\\PC]|".encode("ascii")
    ser.write(frame)
    time.sleep(0.4)
    resp = ser.read(ser.in_waiting or 64)
    decoded = resp.decode("ascii", errors="replace").strip()
    print(f"  TX: {cmd:10s}  →  RX: {decoded or '(sin respuesta)'}")
    return decoded

confirma = input(f"⚠️  Vas a encender el láser {SEGUNDOS}s. ¿Confirmás? (si/no): ")
if confirma.strip().lower() != "si":
    print("Cancelado.")
    exit()

with serial.Serial(PORT, 19200, bytesize=8, parity="N", stopbits=1, timeout=1.0) as ser:
    print("\n[1] Ping...")
    enviar(ser, "SAY")

    print("[2] START")
    enviar(ser, "START")

    print(f"[3] Esperando {SEGUNDOS}s...")
    for i in range(SEGUNDOS, 0, -1):
        print(f"    {i}...")
        time.sleep(1)

    print("[4] STOP")
    enviar(ser, "STOP")

    print("[5] Verificando estado...")
    enviar(ser, "SAY")

print("\nListo.")