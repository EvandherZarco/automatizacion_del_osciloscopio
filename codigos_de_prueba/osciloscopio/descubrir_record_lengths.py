"""
Mini-script: Descubrir record lengths discretos del TDS5054B
Lab PC: CMD, Python 3.11.9 64-bit
Requiere: python-vxi11, numpy

Uso:
    python descubrir_record_lengths.py

Resultado: imprime los NR_PT reales que el scope acepta
por cada combinación de horizontal scale probada.
"""

import vxi11
import time

SCOPE_IP = "192.168.1.100"

# Horizontal scales a probar (segundos/div)
# El scope tiene 10 divisiones → record length varía con cada una
TDIV_VALORES = [
    "2E-9",   # 2 ns/div
    "5E-9",   # 5 ns/div
    "10E-9",  # 10 ns/div
    "20E-9",  # 20 ns/div
    "50E-9",  # 50 ns/div
    "100E-9", # 100 ns/div
    "200E-9", # 200 ns/div
    "500E-9", # 500 ns/div
    "1E-6",   # 1 µs/div
    "2E-6",   # 2 µs/div
    "5E-6",   # 5 µs/div
    "10E-6",  # 10 µs/div
]

def main():
    print(f"Conectando a {SCOPE_IP}...")
    scope = vxi11.Instrument(SCOPE_IP)
    idn = scope.ask("*IDN?")
    print(f"IDN: {idn}\n")

    resultados = []

    for tdiv in TDIV_VALORES:
        # Configurar horizontal scale
        scope.write(f"HOR:MAI:SCA {tdiv}")
        time.sleep(0.3)  # esperar que el scope actualice

        # Preguntar cuántos puntos tiene el record actual
        nr_pt = scope.ask("WFMPRE:NR_PT?").strip()
        pt_off = scope.ask("WFMPRE:PT_OFF?").strip()
        xincr = scope.ask("WFMPRE:XINCR?").strip()

        print(f"TDIV={tdiv:>10} s/div | NR_PT={nr_pt:>8} | PT_OFF={pt_off:>8} | XINCR={xincr}")
        resultados.append((tdiv, nr_pt, pt_off, xincr))

    print("\n--- RESUMEN ---")
    print(f"{'TDIV (s/div)':<15} {'NR_PT':<10} {'PT_OFF':<10} {'XINCR'}")
    print("-" * 55)
    for tdiv, nr_pt, pt_off, xincr in resultados:
        print(f"{tdiv:<15} {nr_pt:<10} {pt_off:<10} {xincr}")

    scope.close()
    print("\nListo. Pega estos valores en el chat para construir el combo box.")

if __name__ == "__main__":
    main()
