r"""
medir_reinicio_numacq.py
Mide en el osciloscopio real cuanto tarda ACQ:NUMACQ? en reiniciarse despues de
ACQ:STATE RUN, con ACQ:MODE AVERAGE y ACQ:STOPAFTER SEQUENCE.

Cada ciclo repite RUN -> STOP -> CURVE? -> (siguiente RUN). Se muestrea
ACQ:NUMACQ?, ACQ:STATE?, ACQ:MODE? y ACQ:STOPAFTER? cada --intervalo segundos
desde --pre segundos antes del RUN hasta --post segundos despues, y tambien
durante --despues segundos tras el STOP y tras el CURVE?. Todo queda en un CSV
con timestamp; un segundo CSV (_resumen) trae una fila por ciclo con:

  numacq_previo      ultima lectura antes de enviar RUN (conteo de la secuencia anterior)
  primer_dato_ms     tiempo del primer muestreo posterior al RUN, desde que se envio
  reinicio_ms        tiempo desde el RUN hasta la primera lectura menor que numacq_previo
                     (vacio si no se vio caida; sin sentido si numacq_previo es 0)
  primer_numacq      primera lectura posterior al RUN
  numacq_final       ultima lectura de la ventana post-RUN
  veredicto          REINICIO_INMEDIATO, REINICIO_RETRASADO, SIN_REINICIO o NO_CONCLUYENTE

El laser debe estar disparando (10 Hz) para que NUMACQ suba tras el reinicio.
Con --numavg grande (por defecto 10000) la secuencia no termina sola durante la
ventana. Al salir se restauran ACQ:MODE, ACQ:NUMAVG, ACQ:STOPAFTER y el estado
RUN/STOP originales.

Uso (CMD):
    venv\Scripts\python.exe herramientas\medir_reinicio_numacq.py
    venv\Scripts\python.exe herramientas\medir_reinicio_numacq.py --host 192.168.1.100 --ciclos 20
    venv\Scripts\python.exe herramientas\medir_reinicio_numacq.py --numavg 100 --post 15
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import vxi11

from app import config_usuario

CAMPOS_MUESTRA = (
    "timestamp", "ciclo", "fase", "evento", "t_s", "t_desde_run_ms", "rtt_numacq_ms",
    "numacq", "state", "mode", "stopafter",
)
CAMPOS_RESUMEN = (
    "ciclo", "numacq_previo", "primer_dato_ms", "reinicio_ms", "primer_numacq",
    "numacq_final", "puntos_curve", "veredicto",
)


class Registro:
    def __init__(self, inst, escritor, intervalo: float):
        self.inst = inst
        self.escritor = escritor
        self.intervalo = intervalo
        self.t0 = time.monotonic()
        self.t_run: float | None = None
        self.ciclo = 0
        self.muestras: list[tuple[str, float, int]] = []

    def evento(self, fase: str, nombre: str) -> None:
        self.escritor.writerow(self._fila(fase, nombre, None, None, None, "", "", ""))

    def _fila(self, fase, evento, numacq, rtt_ms, ahora, state, mode, stopafter) -> dict:
        t_s = "" if ahora is None else f"{ahora - self.t0:.4f}"
        desde_run = ""
        if self.t_run is not None and ahora is not None:
            desde_run = f"{(ahora - self.t_run) * 1000:.1f}"
        return {
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            "ciclo": self.ciclo, "fase": fase, "evento": evento, "t_s": t_s,
            "t_desde_run_ms": desde_run,
            "rtt_numacq_ms": "" if rtt_ms is None else f"{rtt_ms:.1f}",
            "numacq": "" if numacq is None else numacq,
            "state": state, "mode": mode, "stopafter": stopafter,
        }

    def muestrear(self, fase: str, duracion_s: float) -> None:
        """Una lectura cada intervalo, con rejilla fija para no acumular deriva."""
        inicio = time.monotonic()
        k = 0
        while True:
            objetivo = inicio + k * self.intervalo
            if objetivo - inicio > duracion_s:
                break
            espera = objetivo - time.monotonic()
            if espera > 0:
                time.sleep(espera)
            ahora = time.monotonic()
            n = int(self.inst.ask("ACQ:NUMACQ?").strip())
            rtt = (time.monotonic() - ahora) * 1000
            state = self.inst.ask("ACQ:STATE?").strip()
            mode = self.inst.ask("ACQ:MODE?").strip()
            stopafter = self.inst.ask("ACQ:STOPAFTER?").strip()
            self.escritor.writerow(self._fila(fase, "", n, rtt, ahora, state, mode, stopafter))
            self.muestras.append((fase, ahora, n))
            k += 1


def leer_curva(inst) -> int:
    inst.write("CURVE?")
    raw = inst.read_raw()
    if len(raw) < 2 or chr(raw[0]) != "#":
        return 0
    n_digitos = int(chr(raw[1]))
    return len(raw[2 + n_digitos:].rstrip(b"\n")) // 2


def resumir(reg: Registro, t_run: float, t_run_ret: float, previo: int | None, puntos: int) -> dict:
    post = [(t, n) for fase, t, n in reg.muestras if fase == "run"]
    fila = {
        "ciclo": reg.ciclo, "numacq_previo": "" if previo is None else previo,
        "primer_dato_ms": "", "reinicio_ms": "", "primer_numacq": "", "numacq_final": "",
        "puntos_curve": puntos, "veredicto": "NO_CONCLUYENTE",
    }
    if not post:
        return fila
    fila["primer_dato_ms"] = f"{(post[0][0] - t_run) * 1000:.0f}"
    fila["primer_numacq"] = post[0][1]
    fila["numacq_final"] = post[-1][1]
    if not previo:
        return fila
    caida = next(((t, n) for t, n in post if n < previo), None)
    if caida is None:
        fila["veredicto"] = "SIN_REINICIO"
        return fila
    fila["reinicio_ms"] = f"{(caida[0] - t_run) * 1000:.0f}"
    fila["veredicto"] = "REINICIO_INMEDIATO" if caida[0] == post[0][0] else "REINICIO_RETRASADO"
    return fila


def main() -> int:
    p = argparse.ArgumentParser(description="Retraso del reinicio de ACQ:NUMACQ? tras ACQ:STATE RUN.")
    p.add_argument("--host", default=None, help="IP del osciloscopio (por defecto, la de Conexion).")
    p.add_argument("--canal", default="CH1", choices=("CH1", "CH2"))
    p.add_argument("--ciclos", type=int, default=10)
    p.add_argument("--intervalo", type=float, default=0.1, help="periodo de muestreo en s")
    p.add_argument("--pre", type=float, default=0.5, help="s de muestreo antes del RUN")
    p.add_argument("--post", type=float, default=2.0, help="s de muestreo despues del RUN")
    p.add_argument("--despues", type=float, default=0.5, help="s de muestreo tras STOP y tras CURVE?")
    p.add_argument("--numavg", type=int, default=10000)
    p.add_argument("--salida", type=Path, default=None, help="CSV de muestras (el resumen lleva _resumen)")
    args = p.parse_args()

    host = args.host or config_usuario.obtener("OSCIL_HOST")
    salida = args.salida or (
        PROJECT_ROOT / "datos" / "diagnostico_numacq" / f"numacq_{datetime.now():%Y%m%d_%H%M%S}.csv"
    )
    salida.parent.mkdir(parents=True, exist_ok=True)
    resumen_ruta = salida.with_name(salida.stem + "_resumen.csv")

    inst = vxi11.Instrument(host)
    inst.timeout = 30
    print(f"IDN: {inst.ask('*IDN?').strip()}")

    originales = {
        "mode": inst.ask("ACQ:MODE?").strip(),
        "numavg": inst.ask("ACQ:NUMAVG?").strip(),
        "stopafter": inst.ask("ACQ:STOPAFTER?").strip(),
        "state": inst.ask("ACQ:STATE?").strip(),
    }
    resumenes: list[dict] = []
    try:
        inst.write("ACQ:STATE STOP")
        inst.write("ACQ:MODE AVERAGE")
        inst.write(f"ACQ:NUMAVG {args.numavg}")
        inst.write("ACQ:STOPAFTER SEQUENCE")
        inst.write(f"DATA:SOURCE {args.canal}")
        inst.write("DATA:ENCDG RIBINARY")
        inst.write("DATA:WIDTH 2")
        inst.write("DATA:START 1")
        inst.write("DATA:STOP 1000000")
        inst.ask("*OPC?")

        with salida.open("w", encoding="utf-8", newline="") as f_m:
            escritor = csv.DictWriter(f_m, fieldnames=CAMPOS_MUESTRA)
            escritor.writeheader()
            reg = Registro(inst, escritor, args.intervalo)

            # Una secuencia previa que deje NUMACQ > 0, para tener con que comparar.
            inst.write("ACQ:STATE RUN")
            time.sleep(args.post)
            inst.write("ACQ:STATE STOP")
            inst.ask("*OPC?")

            for ciclo in range(1, args.ciclos + 1):
                reg.ciclo = ciclo
                reg.t_run = None
                reg.muestras.clear()
                reg.muestrear("pre", args.pre)
                previo = next((n for fase, _, n in reversed(reg.muestras) if fase == "pre"), None)

                reg.evento("run", "RUN_ENVIADO")
                t_run = time.monotonic()
                reg.t_run = t_run
                inst.write("ACQ:STATE RUN")
                t_run_ret = time.monotonic()
                reg.evento("run", f"RUN_RETORNO_{(t_run_ret - t_run) * 1000:.1f}ms")
                reg.muestrear("run", args.post)

                reg.evento("stop", "STOP_ENVIADO")
                inst.write("ACQ:STATE STOP")
                inst.ask("*OPC?")
                reg.muestrear("stop", args.despues)

                reg.evento("curve", "CURVE_ENVIADO")
                puntos = leer_curva(inst)
                reg.evento("curve", f"CURVE_LEIDO_{puntos}pts")
                reg.muestrear("curve", args.despues)

                fila = resumir(reg, t_run, t_run_ret, previo, puntos)
                resumenes.append(fila)
                print(
                    f"ciclo {ciclo:3d}: previo={fila['numacq_previo']!s:>6} "
                    f"primer={fila['primer_numacq']!s:>6} "
                    f"reinicio_ms={fila['reinicio_ms']!s:>5} {fila['veredicto']}"
                )
                f_m.flush()
    finally:
        try:
            inst.write("ACQ:STATE STOP")
            inst.write(f"ACQ:MODE {originales['mode']}")
            inst.write(f"ACQ:NUMAVG {originales['numavg']}")
            inst.write(f"ACQ:STOPAFTER {originales['stopafter']}")
            if originales["state"] != "0":
                inst.write("ACQ:STATE RUN")
        except Exception as exc:
            print(f"No se pudo restaurar la configuracion original: {exc}")

    with resumen_ruta.open("w", encoding="utf-8", newline="") as f_r:
        w = csv.DictWriter(f_r, fieldnames=CAMPOS_RESUMEN)
        w.writeheader()
        w.writerows(resumenes)
    print(f"\nMuestras: {salida}\nResumen:  {resumen_ruta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
