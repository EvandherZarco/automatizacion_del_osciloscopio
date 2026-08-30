"""
generar_sesion_prueba.py
Genera sesiones sintéticas con la misma estructura que produce el sistema,
para probar visualización, validación de CSV y exportación a MATLAB sin
necesidad de osciloscopio, láser ni ESP32.

Produce dos variantes:
    formato "nuevo"  — header vigente, con columnas de láser
    formato "viejo"  — header anterior a las columnas de láser, equivalente a
                       las sesiones 20260623_182323 y 20260825_192627

Uso:
    python generar_sesion_prueba.py                      genera ambas en ./datos/sesiones
    python generar_sesion_prueba.py "ruta\\destino"       genera ambas en la ruta dada
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

HEADER_NUEVO = [
    "timestamp", "session_id", "medicion_id", "temperatura", "modo",
    "error_flag", "error_desc", "XINCR", "XZERO", "PT_OFF", "YMULT",
    "YOFF", "YZERO", "NR_PT", "CH_SCALE", "HOR_SCALE",
    "output_level", "eo_delay_us", "burst_mode",
    "pulsos_estimados", "archivo_npy",
]

HEADER_VIEJO = [
    "timestamp", "session_id", "medicion_id", "temperatura", "modo",
    "error_flag", "error_desc", "XINCR", "XZERO", "PT_OFF", "YMULT",
    "YOFF", "YZERO", "NR_PT", "CH_SCALE", "HOR_SCALE",
    "pulsos_estimados", "archivo_npy",
]

N_PUNTOS = 5000
XINCR = 2e-9
YMULT = 3.90625e-6
T_ARRIBO_S = 6.8e-6
ANCHO_S = 0.6e-6
FRECUENCIA_HZ = 323e3


def _senal_sintetica(amplitud_mv: float, semilla: int) -> np.ndarray:
    """
    Pulso acústico amortiguado sobre línea base ruidosa, en cuentas del ADC.
    Reproduce la forma general de la señal fotoacústica: arribo cerca de
    6.8 µs y oscilación amortiguada en el modo de espesor del piezoeléctrico.
    """
    rng = np.random.default_rng(semilla)
    t = np.arange(N_PUNTOS) * XINCR

    envolvente = np.exp(-(((t - T_ARRIBO_S) / ANCHO_S) ** 2))
    oscilacion = np.sin(2 * np.pi * FRECUENCIA_HZ * (t - T_ARRIBO_S))
    señal_v = (amplitud_mv * 1e-3) * envolvente * oscilacion
    ruido_v = rng.normal(0.0, amplitud_mv * 1e-3 * 0.04, N_PUNTOS)

    return np.round((señal_v + ruido_v) / YMULT).astype(np.int16)


def _fila(header: list[str], valores: dict) -> list:
    return [valores.get(col, "") for col in header]


def generar(base: Path, formato: str, n_mediciones: int = 6) -> Path:
    nuevo = formato == "nuevo"
    header = HEADER_NUEVO if nuevo else HEADER_VIEJO

    sid = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_prueba_{formato}"
    sesion_dir = base / sid
    sesion_dir.mkdir(parents=True, exist_ok=True)
    csv_path = sesion_dir / f"{sid}.csv"

    inicio = datetime.now()
    temperaturas = np.linspace(60.0, 35.0, n_mediciones)

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow(header)

        for i, temp in enumerate(temperaturas, start=1):
            medicion_id = f"{sid}_m{i:04d}"
            nombre_npy = f"{medicion_id}.npy"

            amplitud = 9.0 + 0.06 * (temp - 35.0)
            raw = _senal_sintetica(amplitud, semilla=i)
            np.save(sesion_dir / nombre_npy, raw)

            con_error = i == n_mediciones - 1

            valores = {
                "timestamp": (inicio + timedelta(minutes=12 * i)).strftime("%Y-%m-%dT%H:%M:%S"),
                "session_id": sid,
                "medicion_id": medicion_id,
                "temperatura": f"{temp:.2f}",
                "modo": "temperatura",
                "error_flag": 1 if con_error else 0,
                "error_desc": "temperatura no detectada" if con_error else "",
                "XINCR": XINCR,
                "XZERO": 0.0,
                "PT_OFF": 0,
                "YMULT": YMULT,
                "YOFF": 0.0,
                "YZERO": 0.0,
                "NR_PT": N_PUNTOS,
                "CH_SCALE": 0.005,
                "HOR_SCALE": 1e-6,
                "output_level": "E Adjust",
                "eo_delay_us": 3800,
                "burst_mode": "Continuous",
                "pulsos_estimados": int(190 + 4 * i),
                "archivo_npy": nombre_npy,
            }
            escritor.writerow(_fila(header, valores))

    print(f"[{formato}] {csv_path}  ({n_mediciones} mediciones, {len(header)} columnas)")
    return csv_path


def main() -> int:
    if len(sys.argv) > 1:
        base = Path(sys.argv[1])
    else:
        base = Path(__file__).resolve().parent.parent / "datos" / "sesiones"

    generar(base, "nuevo")
    generar(base, "viejo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
