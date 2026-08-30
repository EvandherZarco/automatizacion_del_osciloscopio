"""
exportar_mat.py
Convierte las mediciones de una sesion (.csv + .npy) a archivos .mat de MATLAB.
Genera un .mat por medicion dentro de una subcarpeta 'mat' de la sesion.

Uso:
    python exportar_mat.py "ruta\\a\\la\\sesion"
    python exportar_mat.py "ruta\\a\\la\\sesion\\sesion.csv"
    python exportar_mat.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
from scipy.io import savemat

CAMPOS_NUMERICOS = (
    "XINCR", "XZERO", "PT_OFF", "YMULT", "YOFF", "YZERO",
    "NR_PT", "CH_SCALE", "HOR_SCALE", "temperatura", "pulsos_estimados",
    "error_flag",
)


def _a_numero(valor: str):
    if valor is None or valor == "":
        return np.nan
    try:
        return float(valor)
    except ValueError:
        return np.nan


def _localizar_csv(destino: Path) -> Path:
    if destino.is_file() and destino.suffix.lower() == ".csv":
        return destino
    if destino.is_dir():
        candidatos = sorted(destino.glob("*.csv"))
        if len(candidatos) == 1:
            return candidatos[0]
        if not candidatos:
            raise FileNotFoundError(f"No hay ningun CSV en {destino}")
        raise FileNotFoundError(
            f"Hay {len(candidatos)} archivos CSV en {destino}. "
            "Indica cual usar pasando la ruta completa del archivo."
        )
    raise FileNotFoundError(f"Ruta inexistente: {destino}")


def _construir_medicion(fila: dict, raw: np.ndarray) -> dict:
    xincr = _a_numero(fila.get("XINCR"))
    xzero = _a_numero(fila.get("XZERO"))
    pt_off = _a_numero(fila.get("PT_OFF"))
    ymult = _a_numero(fila.get("YMULT"))
    yoff = _a_numero(fila.get("YOFF"))
    yzero = _a_numero(fila.get("YZERO"))

    raw = np.asarray(raw).astype(np.float64).ravel()
    n = raw.size

    voltaje = (raw - yoff) * ymult + yzero
    indices = np.arange(n, dtype=np.float64)
    tiempo = (indices - pt_off) * xincr + xzero

    fs = 1.0 / xincr if xincr and np.isfinite(xincr) and xincr != 0 else np.nan

    metadatos = {}
    for clave, valor in fila.items():
        if clave in CAMPOS_NUMERICOS:
            metadatos[clave] = _a_numero(valor)
        else:
            metadatos[clave] = valor if valor is not None else ""

    return {
        "raw": raw.astype(np.int32),
        "voltaje": voltaje,
        "tiempo": tiempo,
        "fs": fs,
        "n_puntos": float(n),
        "metadatos": metadatos,
    }


def exportar_sesion(destino: str | Path) -> int:
    csv_path = _localizar_csv(Path(destino))
    sesion_dir = csv_path.parent
    salida_dir = sesion_dir / "mat"
    salida_dir.mkdir(exist_ok=True)

    with open(csv_path, "r", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))

    if not filas:
        print(f"El CSV no contiene mediciones: {csv_path}")
        return 0

    print(f"Sesion : {sesion_dir.name}")
    print(f"Salida : {salida_dir}")
    print()

    exportadas = 0
    for fila in filas:
        medicion_id = fila.get("medicion_id", "").strip()
        nombre_npy = fila.get("archivo_npy", "").strip()

        if not medicion_id:
            print("  [omitida] fila sin medicion_id")
            continue

        if not nombre_npy:
            print(f"  [omitida] {medicion_id}: el CSV no registra archivo .npy")
            continue

        npy_path = sesion_dir / nombre_npy
        if not npy_path.exists():
            print(f"  [omitida] {medicion_id}: no se encontro {nombre_npy}")
            continue

        try:
            raw = np.load(npy_path)
        except (OSError, ValueError) as e:
            print(f"  [error]   {medicion_id}: no se pudo leer el .npy ({e})")
            continue

        contenido = _construir_medicion(fila, raw)
        mat_path = salida_dir / f"{medicion_id}.mat"

        try:
            savemat(mat_path, contenido, do_compression=True)
        except (OSError, ValueError) as e:
            print(f"  [error]   {medicion_id}: no se pudo escribir el .mat ({e})")
            continue

        fs_txt = "desconocida" if not np.isfinite(contenido["fs"]) else f"{contenido['fs']/1e6:.1f} MS/s"
        print(f"  [ok]      {mat_path.name}  ({int(contenido['n_puntos'])} puntos, {fs_txt})")
        exportadas += 1

    print()
    print(f"{exportadas} de {len(filas)} mediciones exportadas.")
    return exportadas


def main() -> int:
    if len(sys.argv) > 1:
        destino = Path(sys.argv[1])
    else:
        entrada = input("Ruta de la sesion (carpeta o CSV): ").strip().strip('"')
        if not entrada:
            print("No se indico ninguna ruta.")
            return 1
        destino = Path(entrada)

    try:
        exportar_sesion(destino)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
