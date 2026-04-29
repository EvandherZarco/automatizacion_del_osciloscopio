import serial, time

PORT     = "COM10"
BAUDRATE = 19200

ser = serial.Serial(
    port      = PORT,
    baudrate  = BAUDRATE,
    bytesize  = serial.EIGHTBITS,
    parity    = serial.PARITY_NONE,
    stopbits  = serial.STOPBITS_ONE,
    timeout   = 1.0,
)
ser.reset_input_buffer()
ser.reset_output_buffer()

print(f"Puerto abierto: {ser.name}")

# SAY = ping básico
cmd = b"|[SAY]|"
ser.write(cmd)
print(f"Enviado: {cmd}")

time.sleep(0.5)
resp = ser.read(ser.in_waiting or 64)
print(f"Respuesta: {resp}")

ser.close()
print("Cerrado.")