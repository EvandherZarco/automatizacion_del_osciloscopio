import ctypes, os

DLL_DIR = r"C:\Users\i7\Desktop\automatizacion_Zarco\complementos"
os.chdir(DLL_DIR)

dll = ctypes.WinDLL(os.path.join(DLL_DIR, "REMOTECONTROL64.dll"))
print("DLL cargado correctamente")