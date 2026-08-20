import ctypes, os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DLL_DIR = os.path.normpath(os.path.join(BASE_DIR, "..", "..", "..", "complementos"))
os.chdir(DLL_DIR)

dll = ctypes.WinDLL(os.path.join(DLL_DIR, "REMOTECONTROL64.dll"))
print("DLL cargado correctamente")