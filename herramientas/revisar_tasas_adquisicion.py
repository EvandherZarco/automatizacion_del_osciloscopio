r"""
revisar_tasas_adquisicion.py
Revisa los CSV de sesiones de medicion contra la tasa de disparo del laser.

Para cada fila recalcula tasa = adquisiciones_promediadas / duracion_ventana_s
y lista las filas sospechosas:

  EXCESO          tasa por encima de FREC*(1+tol): el promedio incluye disparos
                  que no corresponden a la ventana.
  DEFICIT         tasa por debajo de FREC*(1-tol): laser sin disparar, ventana
                  truncada o reinicio del contador a mitad de ventana.
  REEVALUADA      fila marcada "sin adquisicion nueva" por el testigo anterior
                  (que leia NUMACQ despues del RUN) cuyo conteo es coherente con
                  la ventana (modo temperatura) o llego a NUMAVG (modo tiempo):
                  el testigo actual no la marcaria. No cuenta como sospechosa.
  SIN_BANDERA     fila con error_flag=0 y conteo incoherente.
  ESCALA          CH_SCALE o HOR_SCALE distintos a los de la fila anterior de
                  la misma sesion, sin bandera de error.
  PROMEDIO        modo AVE con adquisiciones menores que NUMAVG (modo tiempo).

En modo tiempo la ventana no se mide (duracion_ventana_s vacia o 0): solo se
revisa el conteo contra NUMAVG. Las filas manuales no se evaluan por tasa.

Uso (CMD):
    python herramientas\revisar_tasas_adquisicion.py datos\sesion_13112026
    python herramientas\revisar_tasas_adquisicion.py datos\sesion_13112026 --frec 10 --tol 0.15
    python herramientas\revisar_tasas_adquisicion.py ruta\sesion.csv --todas
    python herramientas\revisar_tasas_adquisicion.py datos\sesion_13112026 --todas --salida datos\sesion_13112026\revision_tasas.txt
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path

SIN_ADQUISICION = "sin adquisici"


@dataclass
class Fila:
    csv: Path
    mid: str
    modo: str
    duracion: float
    adq: int
    numavg: int
    acq_mode: str
    error_flag: int
    error_desc: str
    ch_scale: str
    hor_scale: str
    tasa: float | None = None
    motivos: list[str] = field(default_factory=list)
    reevaluada: bool = False


def _num(texto: str, defecto: float = 0.0) -> float:
    try:
        return float(texto)
    except (TypeError, ValueError):
        return defecto


def leer_csv(ruta: Path) -> list[Fila]:
    filas: list[Fila] = []
    with ruta.open(encoding="utf-8", errors="replace", newline="") as f:
        for r in csv.DictReader(f):
            duracion = _num(r.get("duracion_ventana_s", ""))
            filas.append(
                Fila(
                    csv=ruta,
                    mid=r["medicion_id"].split("_")[-1],
                    modo=r["modo"],
                    duracion=duracion,
                    adq=int(_num(r.get("adquisiciones_promediadas", ""))),
                    numavg=int(_num(r.get("numavg", ""))),
                    acq_mode=r.get("acq_mode", ""),
                    error_flag=int(_num(r.get("error_flag", ""))),
                    error_desc=r.get("error_desc", ""),
                    ch_scale=r.get("CH_SCALE", ""),
                    hor_scale=r.get("HOR_SCALE", ""),
                )
            )
    return filas


def evaluar(filas: list[Fila], frec: float, tol: float) -> None:
    anterior: Fila | None = None
    for f in filas:
        marcada_sin_adq = f.error_flag and SIN_ADQUISICION in f.error_desc.lower()
        coherente = True

        if f.modo == "temperatura" and f.duracion > 0:
            f.tasa = f.adq / f.duracion
            if f.tasa > frec * (1 + tol):
                f.motivos.append(f"EXCESO ({f.tasa:.1f}/s > {frec * (1 + tol):.1f}/s)")
                coherente = False
            elif f.tasa < frec * (1 - tol):
                f.motivos.append(f"DEFICIT ({f.tasa:.1f}/s < {frec * (1 - tol):.1f}/s)")
                coherente = False
        elif f.modo == "tiempo":
            if f.acq_mode.upper().startswith("AVE") and f.adq < f.numavg:
                f.motivos.append(f"PROMEDIO ({f.adq} de {f.numavg})")
                coherente = False

        if marcada_sin_adq and coherente:
            f.reevaluada = True
        if not f.error_flag and not coherente and f.modo in ("temperatura", "tiempo"):
            f.motivos.append("SIN_BANDERA")

        if (
            anterior is not None
            and not f.error_flag
            and (f.ch_scale, f.hor_scale) != (anterior.ch_scale, anterior.hor_scale)
        ):
            f.motivos.append(
                f"ESCALA (CH {anterior.ch_scale}->{f.ch_scale}, "
                f"HOR {anterior.hor_scale}->{f.hor_scale})"
            )
        anterior = f


def encontrar_csv(rutas: list[Path]) -> list[Path]:
    encontrados: list[Path] = []
    for ruta in rutas:
        if ruta.is_file():
            encontrados.append(ruta)
        else:
            encontrados.extend(sorted(ruta.rglob("*.csv")))
    return encontrados


class _Tee:
    def __init__(self, *flujos):
        self._flujos = flujos

    def write(self, texto: str) -> int:
        for f in self._flujos:
            f.write(texto)
        return len(texto)

    def flush(self) -> None:
        for f in self._flujos:
            f.flush()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("rutas", nargs="+", type=Path, help="CSV o carpetas con CSV")
    p.add_argument("--frec", type=float, default=10.0, help="frecuencia del laser en Hz")
    p.add_argument("--tol", type=float, default=0.15, help="tolerancia relativa de la tasa")
    p.add_argument("--todas", action="store_true", help="imprime tambien las filas sin sospecha")
    p.add_argument("--salida", type=Path, help="ademas de la consola, guarda la salida en este archivo")
    args = p.parse_args()
    if args.salida is not None:
        args.salida.parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = _Tee(sys.stdout, args.salida.open("w", encoding="utf-8"))

    archivos = encontrar_csv(args.rutas)
    if not archivos:
        print("No se encontraron CSV.")
        return 1

    total = sospechosas = reevaluadas = 0
    for ruta in archivos:
        try:
            filas = leer_csv(ruta)
        except (KeyError, ValueError, OSError) as exc:
            print(f"{ruta}: no se pudo leer ({exc})")
            continue
        evaluar(filas, args.frec, args.tol)
        print(f"\n{ruta}")
        print(f"{'id':6} {'modo':12} {'dur_s':>7} {'adq':>6} {'numavg':>6} {'tasa/s':>7} flag  motivos")
        for f in filas:
            total += 1
            if f.motivos:
                sospechosas += 1
            if f.reevaluada:
                reevaluadas += 1
                f.motivos.append("REEVALUADA (conteo coherente; el testigo actual no la marcaria)")
            if not f.motivos and not args.todas:
                continue
            tasa = f"{f.tasa:7.1f}" if f.tasa is not None else f"{'-':>7}"
            print(
                f"{f.mid:6} {f.modo:12} {f.duracion:7.1f} {f.adq:6d} {f.numavg:6d} "
                f"{tasa} {f.error_flag:4d}  {'; '.join(f.motivos)}"
            )
    print(
        f"\n{sospechosas} filas sospechosas de {total} revisadas "
        f"(frec={args.frec} Hz, tol={args.tol}); "
        f"{reevaluadas} marcadas por el testigo anterior reevaluadas como validas."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
