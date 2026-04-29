import ctypes, os, time

DLL_DIR  = r"C:\Users\i7\Desktop\automatizacion_Zarco\complementos"
os.chdir(DLL_DIR)

dll = ctypes.WinDLL(os.path.join(DLL_DIR, "REMOTECONTROL64.dll"))

dll.rcConnect2.restype  = ctypes.c_int
dll.rcConnect2.argtypes = [
    ctypes.POINTER(ctypes.c_int),
    ctypes.c_int,
    ctypes.c_char_p,
    ctypes.c_char_p,
]

handle   = ctypes.c_int(0)
csv_path = os.path.join(DLL_DIR, "REMOTECONTROL.CSV").encode()

ret = dll.rcConnect2(ctypes.byref(handle), 1, b"\\\\.\\COM10", csv_path)
print(f"rcConnect2 → {ret}")

if ret == 0:
    # Fix 1: más tiempo para que el DLL enumere el CAN bus
    print("Esperando 5s...")
    time.sleep(5)

    # Fix 2: leer como número (u8) en vez de string
    dll.rcGetRegAsDouble2.restype  = ctypes.c_int
    dll.rcGetRegAsDouble2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_double),
    ]

    val = ctypes.c_double(0.0)
    r   = dll.rcGetRegAsDouble2(handle.value, b"NL30x", b"State", ctypes.byref(val))
    print(f"State (double) → {val.value}  (ret={r})")
    # 0=SLEEP, 1=STOP, 2=RUN, 3=FAULT

    dll.rcDisconnect2.restype  = ctypes.c_int
    dll.rcDisconnect2.argtypes = [ctypes.c_int]
    dll.rcDisconnect2(handle.value)
    print("Desconectado.")