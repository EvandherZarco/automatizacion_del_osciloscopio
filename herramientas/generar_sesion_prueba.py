"""
generar_sesion_prueba.py
Genera sesiones sintéticas con la misma estructura que produce el sistema,
para probar visualización, validación de CSV, exportación a MATLAB y las
alertas de verificar_sesion.py sin necesidad de osciloscopio, láser ni ESP32.

Modo variantes de formato (comportamiento original):
    python generar_sesion_prueba.py                      genera "nuevo" y "viejo" en ./datos/sesiones
    python generar_sesion_prueba.py "ruta\\destino"       ídem, en la ruta dada

Modo casos de verificación (uno por cada alerta de verificar_sesion.py):
    python generar_sesion_prueba.py --caso <nombre>       una sesión en datos\\sesiones_prueba\\<nombre>
    python generar_sesion_prueba.py --todos               todos los casos, cada uno en su carpeta

Casos disponibles: ver CASOS_VERIFICACION.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent

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

N_PUNTOS = 25000
XINCR = 2e-9
YMULT = 3.90625e-6
T_ARRIBO_S = 31.4e-6
ANCHO_S = 0.6e-6
FRECUENCIA_HZ = 323e3


def _senal_sintetica(
    amplitud_mv: float,
    semilla: int,
    *,
    n_puntos: int = N_PUNTOS,
    xincr: float = XINCR,
    ymult: float = YMULT,
    t_arribo: float = T_ARRIBO_S,
) -> np.ndarray:
    """
    Pulso acústico sobre línea base ruidosa, en cuentas del ADC. Con el ancho
    de envolvente (0.6 µs) y la frecuencia usados aquí (323 kHz, periodo
    3.1 µs), la envolvente cubre menos de un semiperiodo: el resultado es un
    único pulso de un lóbulo, no una oscilación amortiguada de varios ciclos.
    El resultado se recorta al rango de un ADC de 16 bits, como haría el
    instrumento real, para poder simular saturación sin desbordar el int16.
    """
    rng = np.random.default_rng(semilla)
    t = np.arange(n_puntos) * xincr

    envolvente = np.exp(-(((t - t_arribo) / ANCHO_S) ** 2))
    oscilacion = np.sin(2 * np.pi * FRECUENCIA_HZ * (t - t_arribo))
    señal_v = (amplitud_mv * 1e-3) * envolvente * oscilacion
    ruido_v = rng.normal(0.0, amplitud_mv * 1e-3 * 0.04, n_puntos)

    cuentas = (señal_v + ruido_v) / ymult
    cuentas = np.clip(cuentas, -32768, 32767)
    return np.round(cuentas).astype(np.int16)


@dataclass
class EspecCaptura:
    etiqueta: str
    temperatura: float | None
    semilla: int
    amplitud_mv: float = 9.0
    xincr: float = XINCR
    xzero: float = 0.0
    pt_off: float = 0.0
    ymult: float = YMULT
    yoff: float = 0.0
    yzero: float = 0.0
    nr_pt: int = N_PUNTOS
    t_arribo: float = T_ARRIBO_S
    error_flag: int = 0
    error_desc: str = ""
    escribir_npy: bool = True
    raw_de: "EspecCaptura | None" = None
    modo: str = "temperatura"
    output_level: str = "E Adjust"
    eo_delay_us: int = 3800
    burst_mode: str = "Continuous"
    pulsos_estimados: int | None = None


def _fila(header: list[str], valores: dict) -> list:
    return [valores.get(col, "") for col in header]


def _escribir_metadatos(sesion_dir: Path, sid: str, metadatos: dict | None = None) -> None:
    """
    Réplica independiente de Almacenamiento._escribir_metadatos
    (app\\almacenamiento\\almacenamiento.py); no la importa para evitar
    acoplar este generador a PySide6 y al ciclo de vida completo de
    Almacenamiento. Ver auditoria_verificacion.md: hay dos productores
    del mismo archivo y pueden divergir.
    """
    lineas = [
        f"session_id: {sid}",
        f"fecha_creacion: {datetime.now().isoformat(timespec='seconds')}",
    ]
    for clave, valor in (metadatos or {}).items():
        lineas.append(f"{clave}: {valor if valor not in (None, '') else 'no disponible'}")
    (sesion_dir / "metadatos_sesion.txt").write_text(
        "\n".join(lineas) + "\n", encoding="utf-8"
    )


def generar(
    sesion_dir: Path, sid: str, especificaciones: list[EspecCaptura], header: list[str]
) -> Path:
    sesion_dir.mkdir(parents=True, exist_ok=True)
    csv_path = sesion_dir / f"{sid}.csv"
    inicio = datetime.now()
    crudos: dict[str, np.ndarray] = {}

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow(header)

        for i, spec in enumerate(especificaciones, start=1):
            medicion_id = f"{sid}_{spec.etiqueta}"
            nombre_npy = f"{medicion_id}.npy"

            if spec.raw_de is not None:
                raw = crudos[spec.raw_de.etiqueta]
            else:
                raw = _senal_sintetica(
                    spec.amplitud_mv,
                    spec.semilla,
                    n_puntos=spec.nr_pt,
                    xincr=spec.xincr,
                    ymult=spec.ymult,
                    t_arribo=spec.t_arribo,
                )
            crudos[spec.etiqueta] = raw

            if spec.escribir_npy:
                np.save(sesion_dir / nombre_npy, raw)

            valores = {
                "timestamp": (inicio + timedelta(minutes=12 * i)).strftime("%Y-%m-%dT%H:%M:%S"),
                "session_id": sid,
                "medicion_id": medicion_id,
                "temperatura": "" if spec.temperatura is None else f"{spec.temperatura:.2f}",
                "modo": spec.modo,
                "error_flag": spec.error_flag,
                "error_desc": spec.error_desc,
                "XINCR": spec.xincr,
                "XZERO": spec.xzero,
                "PT_OFF": spec.pt_off,
                "YMULT": spec.ymult,
                "YOFF": spec.yoff,
                "YZERO": spec.yzero,
                "NR_PT": spec.nr_pt,
                "CH_SCALE": 0.005,
                "HOR_SCALE": spec.xincr * spec.nr_pt / 10,
                "output_level": spec.output_level,
                "eo_delay_us": spec.eo_delay_us,
                "burst_mode": spec.burst_mode,
                "pulsos_estimados": (
                    spec.pulsos_estimados if spec.pulsos_estimados is not None else int(190 + 4 * i)
                ),
                "archivo_npy": nombre_npy,
            }
            escritor.writerow(_fila(header, valores))

    _escribir_metadatos(sesion_dir, sid, {})
    return csv_path


# ══════════════════════════════════════════════════════════════════════════
# Variantes de formato (nuevo / viejo) — visualización y exportación a MATLAB
# ══════════════════════════════════════════════════════════════════════════


def _especificaciones_variante(n_mediciones: int) -> list[EspecCaptura]:
    temperaturas = np.linspace(60.0, 35.0, n_mediciones)
    especs = []
    for i, temp in enumerate(temperaturas, start=1):
        amplitud = 9.0 + 0.06 * (temp - 35.0)
        con_error = i == n_mediciones - 1
        especs.append(EspecCaptura(
            etiqueta=f"m{i:04d}",
            temperatura=float(temp),
            semilla=i,
            amplitud_mv=amplitud,
            error_flag=1 if con_error else 0,
            error_desc="temperatura no detectada" if con_error else "",
        ))
    return especs


def generar_variante(base: Path, formato: str, n_mediciones: int = 6) -> Path:
    nuevo = formato == "nuevo"
    header = HEADER_NUEVO if nuevo else HEADER_VIEJO
    sid = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_prueba_{formato}"
    sesion_dir = base / sid

    especs = _especificaciones_variante(n_mediciones)
    csv_path = generar(sesion_dir, sid, especs, header)
    print(f"[{formato}] {csv_path}  ({n_mediciones} mediciones, {len(header)} columnas)")
    return csv_path


# ══════════════════════════════════════════════════════════════════════════
# Casos de verificación — uno por cada alerta de verificar_sesion.py
# ══════════════════════════════════════════════════════════════════════════


def caso_npy_ausente() -> list[EspecCaptura]:
    return [
        EspecCaptura("a", 55.0, semilla=101, amplitud_mv=9.0),
        EspecCaptura("b", 50.0, semilla=102, amplitud_mv=9.3, escribir_npy=False),
        EspecCaptura("c", 45.0, semilla=103, amplitud_mv=9.6),
        EspecCaptura("d", 40.0, semilla=104, amplitud_mv=9.9),
    ]


def caso_sin_reconstruibles() -> list[EspecCaptura]:
    # Inseparable de npy_ausente: "faltantes" (línea 149) se llena con estas
    # mismas etiquetas antes del return temprano que agrega esta alerta.
    return [
        EspecCaptura("a", 55.0, semilla=201, escribir_npy=False),
        EspecCaptura("b", 50.0, semilla=202, escribir_npy=False),
    ]


def caso_saturacion() -> list[EspecCaptura]:
    return [
        EspecCaptura("a", 55.0, semilla=301, amplitud_mv=9.0),
        EspecCaptura("b", 50.0, semilla=302, amplitud_mv=300.0),
        EspecCaptura("c", 45.0, semilla=303, amplitud_mv=9.4),
    ]


def caso_buffer_repetido_misma_escala() -> list[EspecCaptura]:
    especs = [EspecCaptura("a", 55.0, semilla=401, amplitud_mv=9.0)]
    especs.append(EspecCaptura("b", 50.0, semilla=402, amplitud_mv=9.0, raw_de=especs[0]))
    especs.append(EspecCaptura("c", 45.0, semilla=403, amplitud_mv=9.3))
    return especs


def caso_buffer_repetido_escala_distinta() -> list[EspecCaptura]:
    # Inseparable de mezcla_escalas: reusar el mismo buffer crudo con una
    # escala distinta dispara ambas alertas por construcción (confirmado).
    especs = [EspecCaptura("a", 55.0, semilla=501, amplitud_mv=9.0)]
    especs.append(EspecCaptura(
        "b", 50.0, semilla=502, amplitud_mv=9.0, raw_de=especs[0], xincr=XINCR * 2
    ))
    especs.append(EspecCaptura("c", 45.0, semilla=503, amplitud_mv=9.4))
    return especs


def caso_mezcla_escalas() -> list[EspecCaptura]:
    return [
        EspecCaptura("a", 55.0, semilla=601, amplitud_mv=9.0),
        EspecCaptura("b", 50.0, semilla=602, amplitud_mv=9.3, xincr=XINCR * 2),
        EspecCaptura("c", 45.0, semilla=603, amplitud_mv=9.6),
    ]


def caso_deriva_onset() -> list[EspecCaptura]:
    return [
        EspecCaptura("a", 55.0, semilla=701, amplitud_mv=9.0, t_arribo=T_ARRIBO_S - 1.0e-6),
        EspecCaptura("b", 50.0, semilla=702, amplitud_mv=9.3, t_arribo=T_ARRIBO_S),
        EspecCaptura("c", 45.0, semilla=703, amplitud_mv=9.6, t_arribo=T_ARRIBO_S + 1.2e-6),
    ]


def caso_temperatura_estancada() -> list[EspecCaptura]:
    return [
        EspecCaptura("a", 50.0, semilla=801, amplitud_mv=9.00),
        EspecCaptura("b", 50.0, semilla=802, amplitud_mv=9.05),
        EspecCaptura("c", 50.0, semilla=803, amplitud_mv=9.10),
    ]


def caso_error_flag() -> list[EspecCaptura]:
    return [
        EspecCaptura("a", 55.0, semilla=901, amplitud_mv=9.0),
        EspecCaptura(
            "b", 50.0, semilla=902, amplitud_mv=9.3,
            error_flag=1, error_desc="temperatura no detectada",
        ),
        EspecCaptura("c", 45.0, semilla=903, amplitud_mv=9.6),
    ]


def caso_dispersion_vpp() -> list[EspecCaptura]:
    return [
        EspecCaptura("a", 55.0, semilla=1001, amplitud_mv=9.0),
        EspecCaptura("b", 50.0, semilla=1002, amplitud_mv=6.0),
        EspecCaptura("c", 50.0, semilla=1003, amplitud_mv=12.0),
        EspecCaptura("d", 45.0, semilla=1004, amplitud_mv=9.4),
    ]


def caso_limpio() -> list[EspecCaptura]:
    return [
        EspecCaptura("a", 60.0, semilla=1101, amplitud_mv=9.0),
        EspecCaptura("b", 52.0, semilla=1102, amplitud_mv=9.3),
        EspecCaptura("c", 44.0, semilla=1103, amplitud_mv=9.6),
        EspecCaptura("d", 36.0, semilla=1104, amplitud_mv=9.9),
    ]


CASOS_VERIFICACION: dict[str, Callable[[], list[EspecCaptura]]] = {
    "npy_ausente": caso_npy_ausente,
    "sin_reconstruibles": caso_sin_reconstruibles,
    "saturacion": caso_saturacion,
    "buffer_repetido_misma_escala": caso_buffer_repetido_misma_escala,
    "buffer_repetido_escala_distinta": caso_buffer_repetido_escala_distinta,
    "mezcla_escalas": caso_mezcla_escalas,
    "deriva_onset": caso_deriva_onset,
    "temperatura_estancada": caso_temperatura_estancada,
    "error_flag": caso_error_flag,
    "dispersion_vpp": caso_dispersion_vpp,
    "limpio": caso_limpio,
}


def generar_caso(nombre: str, base: Path) -> Path:
    fn = CASOS_VERIFICACION.get(nombre)
    if fn is None:
        raise SystemExit(
            f"Caso desconocido: {nombre}. Use uno de: {', '.join(sorted(CASOS_VERIFICACION))}"
        )

    sesion_dir = base / nombre
    if sesion_dir.exists():
        shutil.rmtree(sesion_dir)

    especs = fn()
    csv_path = generar(sesion_dir, nombre, especs, HEADER_NUEVO)
    print(f"[{nombre}] {csv_path}  ({len(especs)} mediciones)")
    return csv_path


# ══════════════════════════════════════════════════════════════════════════


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Genera sesiones sintéticas para probar el sistema sin hardware."
    )
    parser.add_argument(
        "destino", nargs="?", type=Path, default=None,
        help="Carpeta destino para las variantes nuevo/viejo (por omisión datos\\sesiones).",
    )
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument("--caso", type=str, default=None, help="Genera un solo caso de verificación.")
    grupo.add_argument("--todos", action="store_true", help="Genera todos los casos de verificación.")
    args = parser.parse_args()

    if args.caso or args.todos:
        base = PROJECT_ROOT / "datos" / "sesiones_prueba"
        base.mkdir(parents=True, exist_ok=True)
        if args.todos:
            for nombre in CASOS_VERIFICACION:
                generar_caso(nombre, base)
        else:
            generar_caso(args.caso, base)
        return 0

    base = args.destino if args.destino else PROJECT_ROOT / "datos" / "sesiones"
    generar_variante(base, "nuevo")
    generar_variante(base, "viejo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
