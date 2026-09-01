r"""
verificar_sesion.py
Verificación rápida de una sesión de medición, para correr en el laboratorio
sin salir de la sesión.

Reconstruye cada captura desde su .npy y los parámetros wfmpre del CSV,
calcula Vpp, grafica Vpp contra temperatura y ejecuta una batería de
chequeos de sanidad sobre los datos crudos.

Uso:
    python herramientas\verificar_sesion.py datos\sesiones\20260825_192627
    python herramientas\verificar_sesion.py datos\sesiones\20260825_192627 --sin-grafica
"""

from __future__ import annotations

import argparse
import csv
import sys
from itertools import combinations
from pathlib import Path

import numpy as np

MARGEN_SATURACION = 0.98
UMBRAL_ONSET_SIGMA = 8.0
DISPERSION_ACEPTABLE_PCT = 5.0
TOLERANCIA_ONSET_FRAC = 0.02


class Captura:
    def __init__(self, fila: dict, sesion_dir: Path):
        self.id = fila["medicion_id"]
        self.etiqueta = self.id.split("_")[-1]
        self.timestamp = fila["timestamp"]
        self.modo = fila["modo"]
        self.error_flag = int(fila["error_flag"] or 0)
        self.error_desc = fila.get("error_desc", "")
        self.temperatura = _float(fila.get("temperatura"))
        self.pulsos = _float(fila.get("pulsos_estimados"))
        self.eo_delay = fila.get("eo_delay_us", "")
        self.output_level = fila.get("output_level", "")

        self.xincr = _float(fila["XINCR"])
        self.xzero = _float(fila["XZERO"])
        self.pt_off = _float(fila["PT_OFF"])
        self.ymult = _float(fila["YMULT"])
        self.yoff = _float(fila["YOFF"])
        self.yzero = _float(fila["YZERO"])
        self.nr_pt = _float(fila["NR_PT"])

        self.raw: np.ndarray | None = None
        self.tiempo_us: np.ndarray | None = None
        self.voltaje_mv: np.ndarray | None = None
        self.problema: str | None = None

        npy = sesion_dir / fila["archivo_npy"] if fila["archivo_npy"] else None
        if npy is None or not npy.exists():
            self.problema = "archivo .npy ausente"
            return

        self.raw = np.load(npy)
        n = len(self.raw)
        v = (self.raw.astype(float) - self.yoff) * self.ymult + self.yzero
        t = (np.arange(n) - self.pt_off) * self.xincr + self.xzero

        self.tiempo_us = t * 1e6
        pre = self.tiempo_us < self.tiempo_us[0] + 0.05 * (self.tiempo_us[-1] - self.tiempo_us[0])
        self.linea_base = float(np.median(v[pre]))
        self.voltaje_mv = (v - self.linea_base) * 1e3
        self.ruido_mv = float(self.voltaje_mv[pre].std())

    @property
    def vpp_mv(self) -> float:
        if self.voltaje_mv is None:
            return float("nan")
        return float(self.voltaje_mv.max() - self.voltaje_mv.min())

    @property
    def onset_us(self) -> float:
        if self.voltaje_mv is None or self.ruido_mv == 0:
            return float("nan")
        sobre = np.abs(self.voltaje_mv) > UMBRAL_ONSET_SIGMA * self.ruido_mv
        idx = np.flatnonzero(sobre)
        return float(self.tiempo_us[idx[0]]) if idx.size else float("nan")

    @property
    def pico_us(self) -> float:
        if self.voltaje_mv is None:
            return float("nan")
        return float(self.tiempo_us[np.argmax(np.abs(self.voltaje_mv))])

    @property
    def saturada(self) -> bool:
        if self.raw is None:
            return False
        saturacion_adc = np.iinfo(self.raw.dtype).max
        limite = MARGEN_SATURACION * saturacion_adc
        return bool(np.abs(self.raw.astype(float)).max() >= limite)

    @property
    def escala(self) -> tuple:
        return (self.xincr, self.ymult, self.nr_pt)


def _float(valor) -> float:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return float("nan")


def cargar(sesion_dir: Path) -> list[Captura]:
    candidatos = sorted(sesion_dir.glob("*.csv"))
    if not candidatos:
        raise SystemExit(f"No se encontró CSV en {sesion_dir}")
    with open(candidatos[0], "r", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    return [Captura(fila, sesion_dir) for fila in filas]


def agrupar_por_escala(capturas: list[Captura]) -> dict[tuple, list[Captura]]:
    grupos: dict[tuple, list[Captura]] = {}
    for c in capturas:
        if c.voltaje_mv is None:
            continue
        grupos.setdefault(c.escala, []).append(c)
    return grupos


def tabla(capturas: list[Captura]) -> None:
    print(f"\n{'ID':>6}  {'T °C':>7}  {'Vpp mV':>9}  {'onset µs':>9}  "
          f"{'pico µs':>9}  {'ruido mV':>9}  {'modo':>11}  flag")
    print("-" * 84)
    for c in capturas:
        if c.problema:
            print(f"{c.etiqueta:>6}  {'—':>7}  {c.problema}")
            continue
        print(f"{c.etiqueta:>6}  {c.temperatura:>7.2f}  {c.vpp_mv:>9.3f}  "
              f"{c.onset_us:>9.2f}  {c.pico_us:>9.2f}  {c.ruido_mv:>9.4f}  "
              f"{c.modo:>11}  {c.error_flag}")


def chequeos(capturas: list[Captura]) -> list[str]:
    alertas: list[str] = []
    validas = [c for c in capturas if c.voltaje_mv is not None]

    faltantes = [c.etiqueta for c in capturas if c.problema]
    if faltantes:
        alertas.append(f"Capturas sin .npy: {', '.join(faltantes)}")

    if not validas:
        alertas.append("No hay ninguna captura reconstruible.")
        return alertas

    saturadas = [c.etiqueta for c in validas if c.saturada]
    if saturadas:
        alertas.append(
            f"Señal saturada (toca el techo del ADC): {', '.join(saturadas)}. "
            "Baje la escala vertical del osciloscopio; el Vpp de estas capturas "
            "está recortado y no es comparable."
        )

    for a, b in combinations(validas, 2):
        if a.raw is not None and b.raw is not None and np.array_equal(a.raw, b.raw):
            if a.escala != b.escala:
                alertas.append(
                    f"{a.etiqueta} y {b.etiqueta} tienen datos crudos idénticos bit a bit "
                    "y además escalas distintas. El osciloscopio devolvió el buffer "
                    "anterior sin adquirir de nuevo: el eje temporal reconstruido de "
                    "la segunda captura tampoco es válido."
                )
            else:
                alertas.append(
                    f"{a.etiqueta} y {b.etiqueta} tienen datos crudos idénticos bit a bit. "
                    "El osciloscopio devolvió el buffer anterior: la segunda captura "
                    "no es una adquisición nueva."
                )

    grupos = agrupar_por_escala(validas)
    if len(grupos) > 1:
        detalle = "; ".join(
            f"{len(g)} capturas con XINCR={k[0]:g} YMULT={k[1]:g} NR_PT={k[2]:g}"
            for k, g in grupos.items()
        )
        alertas.append(
            f"La sesión mezcla {len(grupos)} configuraciones de escala ({detalle}). "
            "El Vpp solo es comparable dentro de cada grupo."
        )

    for grupo in grupos.values():
        onsets = np.array([c.onset_us for c in grupo])
        onsets = onsets[~np.isnan(onsets)]
        if onsets.size < 3:
            continue
        span = grupo[0].tiempo_us[-1] - grupo[0].tiempo_us[0]
        deriva = onsets.max() - onsets.min()
        if deriva > TOLERANCIA_ONSET_FRAC * span:
            alertas.append(
                f"El onset se desplaza {deriva:.1f} µs dentro de un mismo grupo de "
                f"escala ({TOLERANCIA_ONSET_FRAC:.0%} del registro es "
                f"{TOLERANCIA_ONSET_FRAC * span:.1f} µs). Alguien movió la posición "
                "horizontal, o el disparo no es estable."
            )

    temps = [c.temperatura for c in validas if not np.isnan(c.temperatura)]
    if temps and len(set(temps)) == 1:
        alertas.append(
            f"Todas las capturas reportan la misma temperatura ({temps[0]:.2f} °C). "
            "El ESP32 probablemente dejó de actualizar."
        )

    con_flag = [c.etiqueta for c in validas if c.error_flag]
    if con_flag:
        alertas.append(f"Capturas con error_flag=1: {', '.join(con_flag)}")

    return alertas


def resumen_por_temperatura(capturas: list[Captura]) -> None:
    validas = [
        c for c in capturas
        if c.voltaje_mv is not None and not np.isnan(c.temperatura)
    ]
    if not validas:
        return

    grupos: dict[tuple[float, tuple], list[float]] = {}
    for c in validas:
        grupos.setdefault((round(c.temperatura, 1), c.escala), []).append(c.vpp_mv)

    print(f"\n{'T °C':>7}  {'escala':>32}  {'n':>3}  {'Vpp medio mV':>13}  {'σ mV':>8}  {'disp %':>7}")
    print("-" * 82)
    for t, escala in sorted(grupos, key=lambda k: (-k[0], k[1])):
        v = np.array(grupos[(t, escala)])
        disp = 100 * v.std() / v.mean() if v.mean() else float("nan")
        marca = "  <-- revisar" if disp > DISPERSION_ACEPTABLE_PCT else ""
        etiqueta_escala = f"XINCR={escala[0]:g} YMULT={escala[1]:g} NR_PT={escala[2]:g}"
        print(f"{t:>7.1f}  {etiqueta_escala:>32}  {len(v):>3}  {v.mean():>13.3f}  {v.std():>8.4f}  "
              f"{disp:>7.2f}{marca}")


def graficar(capturas: list[Captura], sesion_dir: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\nmatplotlib no disponible: se omite la gráfica.")
        return

    validas = [
        c for c in capturas
        if c.voltaje_mv is not None and not np.isnan(c.temperatura)
    ]
    if not validas:
        print("\nSin puntos con temperatura válida: se omite la gráfica.")
        return

    grupos = agrupar_por_escala(validas)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 9))

    for k, grupo in grupos.items():
        t = [c.temperatura for c in grupo]
        v = [c.vpp_mv for c in grupo]
        etiqueta = f"XINCR={k[0]:g} s, YMULT={k[1]:g}, NR_PT={k[2]:g}"
        ax1.plot(t, v, "o", label=etiqueta)

    ax1.set_xlabel("Temperatura (°C)")
    ax1.set_ylabel("Vpp (mV)")
    ax1.set_title(f"Vpp contra temperatura — {sesion_dir.name}")
    ax1.grid(True, alpha=0.3)
    ax1.invert_xaxis()
    if len(grupos) > 1:
        ax1.legend(fontsize=8)

    referencia = max(grupos.values(), key=len)
    for c in referencia[: min(len(referencia), 6)]:
        ax2.plot(c.tiempo_us, c.voltaje_mv, lw=0.7,
                 label=f"{c.etiqueta}  {c.temperatura:.2f} °C")
    ax2.set_xlabel("Tiempo (µs)")
    ax2.set_ylabel("Señal (mV)")
    ax2.set_title("Formas de onda del grupo de escala más numeroso")
    ax2.grid(True, alpha=0.3)
    ax2.legend(fontsize=7)

    fig.tight_layout()
    destino = sesion_dir / "verificacion.png"
    fig.savefig(destino, dpi=130)
    plt.close(fig)
    print(f"\nGráfica guardada en {destino}")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(
        description="Verifica una sesión de medición y grafica Vpp contra temperatura."
    )
    parser.add_argument("sesion", type=Path, help="Carpeta de la sesión")
    parser.add_argument("--sin-grafica", action="store_true")
    args = parser.parse_args()

    sesion_dir = args.sesion.resolve()
    if not sesion_dir.is_dir():
        raise SystemExit(f"No es una carpeta: {sesion_dir}")

    capturas = cargar(sesion_dir)
    print(f"Sesión: {sesion_dir.name}   capturas: {len(capturas)}")

    tabla(capturas)
    resumen_por_temperatura(capturas)

    alertas = chequeos(capturas)
    print("\n" + "=" * 84)
    if alertas:
        print("ALERTAS")
        for a in alertas:
            print(f"  - {a}")
    else:
        print("Sin alertas. Los datos se ven consistentes.")
    print("=" * 84)

    if not args.sin_grafica:
        graficar(capturas, sesion_dir)

    return 1 if alertas else 0


if __name__ == "__main__":
    sys.exit(main())
