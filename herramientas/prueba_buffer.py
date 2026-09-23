r"""
prueba_buffer.py
Prueba en hardware de la frescura del registro del osciloscopio, usando
ACQ:NUMACQ? como testigo antes y después de cada cambio de escala por SCPI.

Medido en el TDS5054B: ACQ:STATE RUN no reinicia NUMACQ; un cambio de escala
sí, pero con retraso (menos de ~1 s), así que justo después del cambio el
contador conserva el valor viejo. Por eso NUMACQ se muestrea durante toda la
espera y se juzga con TestigoNumacq, el mismo criterio de la aplicación: el
registro es fresco si el contador subió sin caer, o si cayó (reinicio) y
luego subió; en AVERAGE está completo si las adquisiciones desde el último
reinicio llegan a NUMAVG. El MD5 de la curva se informa pero no es testigo:
en STOP, cambiar la escala cambia el MD5 sin adquisición nueva.

Para cada modo de adquisición pedido y cada escala horizontal de la lista,
en RUN y en STOP: lee NUMACQ, cambia HORizontal:SCAle, muestrea NUMACQ
durante --espera segundos y lee ACQ:MODE?, ACQ:NUMAVG?, WFMPRE:NR_PT? y
CURVE?. Al arrancar informa TRIGger:A:MODe? y la tasa de adquisiciones; con
el trigger fuera de NORMAL el osciloscopio adquiere sin disparo del láser.

La escala, el modo, NUMAVG, STOPAFTER y el estado RUN/STOP originales se
restauran al salir.

Uso:
    venv\Scripts\python.exe herramientas\prueba_buffer.py
    venv\Scripts\python.exe herramientas\prueba_buffer.py --host 192.168.1.100 --espera 3
    venv\Scripts\python.exe herramientas\prueba_buffer.py --modos SAMPLE AVERAGE --numavg 16 --espera 3
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import vxi11

from app import config_usuario
from app.osciloscopio.control_osciloscopio import RETRASO_REINICIO_S, TestigoNumacq

ESCALAS_S = [1e-5, 4e-5, 2e-5, 4e-6, 1e-5]
MUESTREO_S = 0.05


def numacq(inst) -> int:
    return int(inst.ask("ACQ:NUMACQ?").strip())


def estado(inst) -> str:
    return "RUN" if inst.ask("ACQ:STATE?").strip() == "1" else "STOP"


def leer_curva(inst) -> tuple[int, int, str]:
    record = int(inst.ask("HORizontal:RECOrdlength?").strip())
    inst.write("DATA:START 1")
    inst.write(f"DATA:STOP {record}")
    nr_pt = int(inst.ask("WFMPRE:NR_PT?").strip())
    inst.write("CURVE?")
    raw = inst.read_raw()
    n_digits = int(chr(raw[1]))
    datos = raw[2 + n_digits:].rstrip(b"\n")
    return nr_pt, len(datos) // 2, hashlib.md5(datos).hexdigest()[:10]


def medir_tasa(inst, duracion_s: float) -> float | None:
    """Adquisiciones por segundo en RUN, sin tocar la escala; None si hubo reinicio."""
    n0, t0 = numacq(inst), time.monotonic()
    time.sleep(duracion_s)
    n1, t1 = numacq(inst), time.monotonic()
    return (n1 - n0) / (t1 - t0) if n1 >= n0 else None


def muestrear(inst, testigo: TestigoNumacq, t_cambio: float, espera_s: float) -> tuple[float | None, float | None]:
    """
    Registra NUMACQ en el testigo durante la espera. Devuelve el retraso del
    primer reinicio visto respecto al cambio de escala y la tasa de
    adquisiciones en el tramo posterior al último reinicio (o en toda la
    espera si no lo hubo).
    """
    retraso = None
    tramo = [(time.monotonic(), testigo.ultimo)]
    fin = t_cambio + espera_s
    while time.monotonic() < fin:
        time.sleep(MUESTREO_S)
        ahora = time.monotonic()
        if testigo.registrar(numacq(inst)):
            if retraso is None:
                retraso = ahora - t_cambio
            tramo = []
        tramo.append((ahora, testigo.ultimo))
    tasa = None
    if len(tramo) >= 2 and tramo[-1][0] > tramo[0][0]:
        tasa = (tramo[-1][1] - tramo[0][1]) / (tramo[-1][0] - tramo[0][0])
    return retraso, tasa


def barrer(inst, condicion: str, espera_s: float) -> list[dict]:
    if condicion == "RUN":
        inst.write("ACQ:STOPAFTER RUNSTOP")
        inst.write("ACQ:STATE RUN")
    else:
        inst.write("ACQ:STATE STOP")
    inst.ask("*OPC?")

    filas = []
    md5_prev = leer_curva(inst)[2]
    for escala in ESCALAS_S:
        n_antes = numacq(inst)
        t_cambio = time.monotonic()
        inst.write(f"HORizontal:SCAle {escala:g}")
        inst.ask("*OPC?")
        n_tras_cambio = numacq(inst)
        testigo = TestigoNumacq(n_antes)
        testigo.registrar(n_tras_cambio)
        retraso, tasa = muestrear(inst, testigo, t_cambio, espera_s)
        modo = inst.ask("ACQ:MODE?").strip().upper()
        numavg = int(inst.ask("ACQ:NUMAVG?").strip())
        nr_pt, n_curva, md5 = leer_curva(inst)
        filas.append({
            "modo": modo,
            "numavg": numavg,
            "condicion": condicion,
            "estado": estado(inst),
            "escala": escala,
            "n_antes": n_antes,
            "n_tras_cambio": n_tras_cambio,
            "testigo": testigo,
            "retraso": retraso,
            "tasa": tasa,
            "nr_pt": nr_pt,
            "n_curva": n_curva,
            "md5": md5,
            "md5_distinto": md5 != md5_prev,
        })
        md5_prev = md5
    return filas


def probar_reinicio_run(inst, espera_s: float) -> tuple[int, int, int]:
    inst.write("ACQ:STOPAFTER RUNSTOP")
    inst.write("ACQ:STATE RUN")
    time.sleep(espera_s)
    inst.write("ACQ:STATE STOP")
    inst.ask("*OPC?")
    n_stop = numacq(inst)
    inst.write("ACQ:STATE RUN")
    n_run = numacq(inst)
    time.sleep(espera_s)
    return n_stop, n_run, numacq(inst)


def veredicto(f: dict) -> str:
    t = f["testigo"]
    if f["nr_pt"] != f["n_curva"]:
        return "NR_PT ≠ len: metadatos de otro registro"
    if not t.fresca:
        return "sin adquisición nueva"
    if f["modo"].startswith("AVE") and t.desde_reinicio < f["numavg"]:
        return f"promedio incompleto ({t.desde_reinicio} de {f['numavg']})"
    return "fresca"


def imprimir(filas: list[dict]) -> None:
    print(f"\n{'ACQ:MODE':<9} {'NUMAVG':>6} {'cond':<5} {'estado':<6} {'escala':>8} "
          f"{'N antes':>8} {'N cambio':>9} {'N final':>8} {'reinicio':>9} {'desde rein.':>11} "
          f"{'adq/s':>6} {'NR_PT':>7} {'len':>7} {'md5_distinto':>12}  veredicto")
    for f in filas:
        t = f["testigo"]
        reinicio = f"{f['retraso'] * 1000:.0f} ms" if f["retraso"] is not None else "—"
        tasa = f"{f['tasa']:.1f}" if f["tasa"] is not None else "—"
        print(f"{f['modo']:<9} {f['numavg']:>6} {f['condicion']:<5} {f['estado']:<6} "
              f"{f['escala']:>8.0e} {f['n_antes']:>8} {f['n_tras_cambio']:>9} {t.ultimo:>8} "
              f"{reinicio:>9} {t.desde_reinicio:>11} {tasa:>6} {f['nr_pt']:>7} {f['n_curva']:>7} "
              f"{'sí' if f['md5_distinto'] else 'no':>12}  {veredicto(f)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Frescura del registro con NUMACQ como testigo.")
    parser.add_argument("--host", default=None, help="IP del osciloscopio (por defecto, la de Conexión).")
    parser.add_argument("--canal", default="CH1", choices=("CH1", "CH2"))
    parser.add_argument("--espera", type=float, default=2.0,
                        help="Segundos de muestreo de NUMACQ tras cada cambio de escala (default 2.0).")
    parser.add_argument("--modos", nargs="+", default=None, choices=("SAMPLE", "AVERAGE"),
                        help="Modos de adquisición a probar (por defecto, el modo actual).")
    parser.add_argument("--numavg", type=int, default=None,
                        help="ACQ:NUMAVG a usar en AVERAGE (por defecto, el valor actual).")
    args = parser.parse_args()

    host = args.host or config_usuario.obtener("OSCIL_HOST")
    inst = vxi11.Instrument(host)
    inst.timeout = 30.0
    print(inst.ask("*IDN?").strip())

    inst.write(f"DATA:SOURCE {args.canal}")
    inst.write("DATA:ENCDG RIBINARY")
    inst.write("DATA:WIDTH 2")

    escala_original = inst.ask("HORizontal:SCAle?").strip()
    estado_original = estado(inst)
    stopafter_original = inst.ask("ACQ:STOPAFTER?").strip()
    modo_original = inst.ask("ACQ:MODE?").strip().upper()
    numavg_original = inst.ask("ACQ:NUMAVG?").strip()
    trigger = inst.ask("TRIGger:A:MODe?").strip().upper()
    print(f"Estado inicial: {estado_original}, STOPAFTER {stopafter_original}, "
          f"escala {escala_original} s/div, ACQ:MODE {modo_original}, NUMAVG {numavg_original}, "
          f"TRIGger:A:MODe {trigger}")
    if not trigger.startswith("NORM"):
        print(f"AVISO: el trigger está en {trigger}, no en NORMAL. El osciloscopio adquiere "
              "aunque el láser no dispare: NUMACQ y la tasa no dependen del láser.")
    if args.espera < RETRASO_REINICIO_S:
        print(f"AVISO: --espera {args.espera:g} s es menor que el retraso del reinicio "
              f"(~{RETRASO_REINICIO_S:g} s); un reinicio puede no alcanzar a verse.")

    try:
        inst.write("ACQ:STOPAFTER RUNSTOP")
        inst.write("ACQ:STATE RUN")
        tasa = medir_tasa(inst, args.espera)
        print("Tasa de adquisición en RUN: "
              + (f"{tasa:.1f} adq/s" if tasa is not None else "no medible (el contador se reinició)"))

        filas = []
        for modo in args.modos or [modo_original]:
            inst.write(f"ACQ:MODE {modo}")
            if modo.startswith("AVE") and args.numavg:
                inst.write(f"ACQ:NUMAVG {args.numavg}")
            filas += barrer(inst, "RUN", args.espera)
            filas += barrer(inst, "STOP", args.espera)
        imprimir(filas)

        n_stop, n_run, n_run_despues = probar_reinicio_run(inst, args.espera)
        print(f"\nNUMACQ en STOP: {n_stop} → justo tras ACQ:STATE RUN: {n_run} "
              f"→ {args.espera:g} s después: {n_run_despues}")
        if n_run < n_stop or n_run_despues < n_stop:
            print("ACQ:STATE RUN reinicia el contador.")
        else:
            print("ACQ:STATE RUN no reinicia el contador.")

        retrasos = [f["retraso"] for f in filas if f["retraso"] is not None]
        print(f"Cambios de escala con reinicio visible de NUMACQ: {len(retrasos)} de {len(filas)}"
              + (f" (retraso {min(retrasos) * 1000:.0f}–{max(retrasos) * 1000:.0f} ms)."
                 if retrasos else "."))

        for modo in sorted({f["modo"] for f in filas}):
            for condicion in ("RUN", "STOP"):
                grupo = [f for f in filas if f["modo"] == modo and f["condicion"] == condicion]
                frescas = sum(1 for f in grupo if veredicto(f) == "fresca")
                md5 = sum(1 for f in grupo if f["md5_distinto"])
                print(f"{modo} en {condicion}: frescas {frescas} de {len(grupo)}; "
                      f"md5_distinto {md5} de {len(grupo)}.")
        print("El MD5 no es testigo de adquisición: en STOP cambia con la escala sin adquirir.")
    finally:
        inst.write(f"ACQ:MODE {modo_original}")
        inst.write(f"ACQ:NUMAVG {numavg_original}")
        inst.write(f"HORizontal:SCAle {escala_original}")
        inst.write(f"ACQ:STOPAFTER {stopafter_original}")
        inst.write("ACQ:STATE RUN" if estado_original == "RUN" else "ACQ:STATE STOP")
        print(f"\nRestaurado: escala {escala_original} s/div, ACQ:MODE {modo_original}, "
              f"NUMAVG {numavg_original}, {estado(inst)}.")
        inst.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
