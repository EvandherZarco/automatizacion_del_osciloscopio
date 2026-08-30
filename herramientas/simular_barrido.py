"""
simular_barrido.py
Simulador del modo temperatura completo, sin ESP32, láser ni osciloscopio.

Comprime en minutos un barrido que en el laboratorio toma horas, para
ejercitar la máquina de estados de TriggerWorker (y, en el nivel dos, la
cadena completa vía Medicion) antes de una sesión real.

Los dobles no abren ningún puerto serie ni socket: TriggerWorker solo
necesita un objeto con consultar() -> (temp, sensores, es_fresco), la misma
firma que TempWorker.consultar().

Uso:
    python herramientas\\simular_barrido.py            corre los 8 casos + nivel 2
    python herramientas\\simular_barrido.py 3           corre solo el caso 3
"""

from __future__ import annotations

import argparse
import math
import random
import re
import sys
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
from PySide6.QtCore import QCoreApplication, QTimer

from app.medicion import trigger

TriggerWorker = trigger.TriggerWorker

RESOLUCION_SENSOR = 0.0625  # resolución real del DS18B20


# ══════════════════════════════════════════════════════════════════════════
# Doble de temperatura
# ══════════════════════════════════════════════════════════════════════════


class DobleTemperaturaSimulada:
    """
    Doble de TempWorker: curva de enfriamiento de Newton comprimida en el
    tiempo, con ruido gaussiano, cuantización del sensor, un modo de falla
    (desconexión) y un modo de rebote (ascenso transitorio).

    fallo_real y rebote_real se expresan en segundos reales transcurridos
    desde la creación del objeto, no en tiempo simulado — así el script de
    prueba puede pedir p. ej. "45 s reales de corte" sin tener que invertir
    a mano la curva de enfriamiento.
    """

    def __init__(
        self,
        T0: float,
        T_amb: float,
        tau: float = 10800.0,
        factor_compresion: float = 100.0,
        ruido_std: float = 0.05,
        fallo_real: tuple[float, float | None] | None = None,
        rebote_real: tuple[float, float, float] | None = None,
        semilla: int | None = None,
    ):
        self.T0 = T0
        self.T_amb = T_amb
        self.tau = tau
        self.factor_compresion = factor_compresion
        self.ruido_std = ruido_std
        self.fallo_real = fallo_real
        self.rebote_real = rebote_real
        self._semilla = semilla
        self._t0_real = time.monotonic()

    def tiempo_real_transcurrido(self) -> float:
        return time.monotonic() - self._t0_real

    def tiempo_simulado(self) -> float:
        return self.tiempo_real_transcurrido() * self.factor_compresion

    def consultar(self) -> tuple[float | None, list[bool], bool]:
        t_real = self.tiempo_real_transcurrido()

        if self.fallo_real is not None:
            ini, fin = self.fallo_real
            if ini <= t_real and (fin is None or t_real <= fin):
                return None, [False, False, False, False], False

        t_sim = t_real * self.factor_compresion
        temp = self.T_amb + (self.T0 - self.T_amb) * math.exp(-t_sim / self.tau)

        if self.rebote_real is not None:
            ini, duracion, magnitud = self.rebote_real
            if ini <= t_real <= ini + duracion:
                temp += magnitud

        if self.ruido_std > 0:
            # Ruido determinado por (semilla, t_sim) y no por orden de llamada:
            # así una lectura extra del colector (para registrar la tabla) no
            # perturba la secuencia de ruido que ve el propio TriggerWorker,
            # y la corrida es reproducible pese al jitter real del scheduler.
            r = random.Random(f"{self._semilla}:{round(t_sim, 4)}")
            temp += r.gauss(0.0, self.ruido_std)

        temp = round(temp / RESOLUCION_SENSOR) * RESOLUCION_SENSOR

        return temp, [True, True, True, True], True


def t_sim_para_temp(T0: float, T_amb: float, tau: float, T: float) -> float:
    """Instante simulado en que la curva de Newton cruza la temperatura T."""
    return tau * math.log((T0 - T_amb) / (T - T_amb))


def duracion_esperada_sim(T_amb: float, tau: float, objetivo: float, margen: float) -> float:
    """
    Duración simulada teórica de la ventana de integración de un objetivo:
    tiempo entre cruzar objetivo+margen y objetivo-margen. No depende de T0.
    """
    return tau * math.log((objetivo + margen - T_amb) / (objetivo - margen - T_amb))


def duracion_real_total_estimada(
    T0: float, T_amb: float, tau: float, factor: float, objetivo_final: float, margen: float
) -> float:
    """Segundos reales estimados desde el arranque hasta cerrar el último objetivo."""
    return t_sim_para_temp(T0, T_amb, tau, objetivo_final - margen) / factor


# ══════════════════════════════════════════════════════════════════════════
# Colector de señales
# ══════════════════════════════════════════════════════════════════════════


class ColectorSenales:
    """
    Escucha las señales de un TriggerWorker y registra, para cada ventana de
    integración, el instante real y simulado de apertura/cierre y la
    temperatura vigente. No es un QObject: como todo corre en el hilo
    principal, las conexiones de PySide son directas y no requieren un
    event loop activo.
    """

    def __init__(self, tw: TriggerWorker, doble: DobleTemperaturaSimulada):
        self._doble = doble
        self._pendientes: deque[float] = deque(tw._generar_objetivos())
        self.objetivos_totales = list(self._pendientes)
        self.ventanas: list[dict] = []
        self.advertencias: list[str] = []
        self._actual: dict | None = None
        self.terminada = False

        tw.iniciar_acumulacion.connect(self._on_iniciar)
        tw.detener_y_capturar.connect(self._on_detener)
        tw.advertencia.connect(self._on_advertencia)
        tw.secuencia_terminada.connect(self._on_terminada)

    def _on_iniciar(self):
        temp, _, _ = self._doble.consultar()
        objetivo = self._pendientes.popleft() if self._pendientes else None
        self._actual = {
            "objetivo": objetivo,
            "t_real_ini": self._doble.tiempo_real_transcurrido(),
            "t_sim_ini": self._doble.tiempo_simulado(),
            "temp_apertura": temp,
        }

    def _on_detener(self):
        temp, _, _ = self._doble.consultar()
        if self._actual is None:
            return
        v = self._actual
        v["t_real_fin"] = self._doble.tiempo_real_transcurrido()
        v["t_sim_fin"] = self._doble.tiempo_simulado()
        v["temp_cierre"] = temp
        v["duracion_real"] = v["t_real_fin"] - v["t_real_ini"]
        v["duracion_sim"] = v["t_sim_fin"] - v["t_sim_ini"]
        v["pulsos_estimados"] = v["duracion_sim"] * 10.0
        self.ventanas.append(v)
        self._actual = None

    def _on_advertencia(self, mensaje: str):
        self.advertencias.append(mensaje)

        m = re.search(
            r"Se omiten los objetivos por encima de esa temperatura: (.+) °C\.", mensaje
        )
        if m:
            omitidos = [x.strip() for x in m.group(1).split(",")]
            for _ in omitidos:
                if self._pendientes:
                    self._pendientes.popleft()
            return

        # El objetivo vigente se pierde por una reconexión con la ventana ya
        # rebasada: trigger.py ya lo descartó de su propia lista sin emitir
        # iniciar_acumulacion, así que _on_iniciar nunca lo va a sacar de
        # _pendientes — hay que hacerlo aquí para no desalinear las
        # etiquetas de los objetivos que siguen.
        if re.search(r"^El objetivo [\d.]+ °C se perdió durante la interrupción", mensaje):
            if self._pendientes:
                self._pendientes.popleft()
            return

        m = re.search(r"Se omiten además los objetivos ya rebasados: (.+) °C\.", mensaje)
        if m:
            omitidos = [x.strip() for x in m.group(1).split(",")]
            for _ in omitidos:
                if self._pendientes:
                    self._pendientes.popleft()

    def _on_terminada(self):
        self.terminada = True

    def imprimir_tabla(self):
        encabezado = (
            f"{'Objetivo':>10} {'T apertura':>12} {'T cierre':>10} "
            f"{'Dur. ventana (s sim)':>22} {'Pulsos est.':>12}"
        )
        print(encabezado)
        print("-" * len(encabezado))
        if not self.ventanas:
            print("  (sin ventanas)")
            return
        for v in self.ventanas:
            obj = f"{v['objetivo']:.1f}" if v["objetivo"] is not None else "?"
            ta = f"{v['temp_apertura']:.2f}" if v["temp_apertura"] is not None else "?"
            tc = f"{v['temp_cierre']:.2f}" if v["temp_cierre"] is not None else "?"
            print(f"{obj:>10} {ta:>12} {tc:>10} {v['duracion_sim']:>22.1f} {v['pulsos_estimados']:>12.1f}")


# ══════════════════════════════════════════════════════════════════════════
# Dobles para el nivel dos (cadena completa)
# ══════════════════════════════════════════════════════════════════════════


class DobleLaser:
    def start(self) -> bool:
        return True

    def leer_parametros(self) -> dict:
        return {"output_level": "50%", "eo_delay_us": 3800, "burst_mode": "Single Shot"}


class DobleModoSeguro:
    def activar(self) -> bool:
        return True


class DobleOsciloscopio:
    def __init__(self, nr_pt: int = 1000):
        self._nr_pt = nr_pt

    def configurar_modo_temperatura(self) -> bool:
        return True

    def acq_run(self) -> bool:
        return True

    def acq_stop_and_capture(self):
        from app.osciloscopio.control_osciloscopio import CapturaOscil

        wfmpre = {
            "XINCR": 4e-9,
            "XZERO": 0.0,
            "PT_OFF": 0,
            "YMULT": 0.002,
            "YOFF": 0.0,
            "YZERO": 0.0,
            "NR_PT": self._nr_pt,
            "CH_SCALE": 0.05,
            "HOR_SCALE": 1e-5,
        }
        raw = (np.random.randn(self._nr_pt) * 100).astype(np.int16)
        return CapturaOscil(raw_data=raw, wfmpre=wfmpre, voltaje=np.zeros(1), tiempo=np.zeros(1))

    def cancelar_espera(self) -> None:
        pass


class DobleMonitor:
    def pausar_pings(self):
        pass

    def reanudar_pings(self):
        pass


# ══════════════════════════════════════════════════════════════════════════
# Casos de prueba
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class ResultadoCaso:
    nombre: str
    aprobado: bool
    detalle: str = ""


def caso_1_barrido_nominal() -> ResultadoCaso:
    print("\n=== Caso 1 — Barrido nominal ===")
    # factor bajo a propósito: con MARGEN_UMBRAL=0.1 la ventana del objetivo
    # más caliente (60) dura ~55 s simulados; un factor mayor deja que un
    # solo POLL_TEMP_S cubra toda la banda y la duración medida colapsa al
    # piso de un sondeo, por debajo de la mitad de lo esperado.
    T0, T_amb, tau, factor = 62.0, 21.0, 10800.0, 20.0
    margen = trigger.MARGEN_UMBRAL

    doble = DobleTemperaturaSimulada(T0=T0, T_amb=T_amb, tau=tau, factor_compresion=factor, semilla=1)
    tw = TriggerWorker(modo="temperatura", temp_worker=doble, t_inicial=60.0, t_final=30.0, paso=5.0)
    colector = ColectorSenales(tw, doble)
    tw.iniciar()
    colector.imprimir_tabla()

    n = len(colector.ventanas)
    orden_ok = all(v["objetivo"] is not None for v in colector.ventanas) and all(
        colector.ventanas[i]["objetivo"] > colector.ventanas[i + 1]["objetivo"] for i in range(n - 1)
    )
    pulsos_ok = n > 0 and all(v["pulsos_estimados"] > 0 for v in colector.ventanas)

    duraciones_ok = True
    notas = []
    for v in colector.ventanas:
        esperado = duracion_esperada_sim(T_amb, tau, v["objetivo"], margen)
        if v["duracion_sim"] < esperado / 2:
            duraciones_ok = False
            notas.append(
                f"objetivo {v['objetivo']}: duración {v['duracion_sim']:.1f} s sim "
                f"< mitad de la esperada {esperado:.1f} s sim"
            )

    aprobado = n == 7 and orden_ok and duraciones_ok and pulsos_ok
    detalle = f"ventanas={n} orden_descendente={orden_ok} duraciones_ok={duraciones_ok} pulsos_ok={pulsos_ok}"
    if notas:
        detalle += "; " + "; ".join(notas)
    return ResultadoCaso("Caso 1 — Barrido nominal", aprobado, detalle)


def caso_2_arranque_tibio() -> ResultadoCaso:
    print("\n=== Caso 2 — Arranque con la muestra ya tibia ===")
    T0, T_amb, tau, factor = 47.0, 21.0, 10800.0, 200.0

    doble = DobleTemperaturaSimulada(T0=T0, T_amb=T_amb, tau=tau, factor_compresion=factor, semilla=2)
    tw = TriggerWorker(modo="temperatura", temp_worker=doble, t_inicial=60.0, t_final=30.0, paso=5.0)
    colector = ColectorSenales(tw, doble)
    tw.iniciar()
    colector.imprimir_tabla()
    for a in colector.advertencias:
        print(f"  [advertencia] {a}")

    omitidos_esperados = {60.0, 55.0, 50.0}
    omitidos_detectados: set[float] = set()
    for msg in colector.advertencias:
        m = re.search(r"Se omiten los objetivos por encima de esa temperatura: (.+) °C\.", msg)
        if m:
            omitidos_detectados = {float(x.strip()) for x in m.group(1).split(",")}

    objetivos_restantes = [v["objetivo"] for v in colector.ventanas]
    restantes_ok = objetivos_restantes == [45.0, 40.0, 35.0, 30.0]

    aprobado = bool(omitidos_detectados) and omitidos_detectados == omitidos_esperados and restantes_ok
    detalle = f"omitidos={sorted(omitidos_detectados)} ventanas={objetivos_restantes}"
    return ResultadoCaso("Caso 2 — Arranque con la muestra ya tibia", aprobado, detalle)


def caso_3_ruido_elevado() -> ResultadoCaso:
    print("\n=== Caso 3 — Ruido elevado ===")
    T0, T_amb, tau, factor = 62.0, 21.0, 10800.0, 100.0

    doble = DobleTemperaturaSimulada(
        T0=T0, T_amb=T_amb, tau=tau, factor_compresion=factor, ruido_std=0.3, semilla=3
    )
    tw = TriggerWorker(modo="temperatura", temp_worker=doble, t_inicial=60.0, t_final=30.0, paso=5.0)
    colector = ColectorSenales(tw, doble)
    tw.iniciar()
    colector.imprimir_tabla()

    n = len(colector.ventanas)
    aprobado = n == 7
    detalle = f"ventanas={n} (se esperaban 7)"
    return ResultadoCaso("Caso 3 — Ruido elevado", aprobado, detalle)


def caso_4_desconexion_breve() -> ResultadoCaso:
    print("\n=== Caso 4 — Desconexión breve ===")
    T0, T_amb, tau, factor = 62.0, 21.0, 10800.0, 150.0
    margen = trigger.MARGEN_UMBRAL

    total_real = duracion_real_total_estimada(T0, T_amb, tau, factor, 30.0, margen)
    t_ini = total_real * 0.4
    doble = DobleTemperaturaSimulada(
        T0=T0, T_amb=T_amb, tau=tau, factor_compresion=factor,
        fallo_real=(t_ini, t_ini + 45.0), semilla=4,
    )
    tw = TriggerWorker(modo="temperatura", temp_worker=doble, t_inicial=60.0, t_final=30.0, paso=5.0)
    colector = ColectorSenales(tw, doble)
    tw.iniciar()
    colector.imprimir_tabla()
    for a in colector.advertencias:
        print(f"  [advertencia] {a}")

    idx_interrumpida = next((i for i, a in enumerate(colector.advertencias) if "interrumpida" in a), None)
    idx_restablecida = next((i for i, a in enumerate(colector.advertencias) if "restablecida" in a), None)
    orden_ok = idx_interrumpida is not None and idx_restablecida is not None and idx_interrumpida < idx_restablecida

    # Con la corrección de trigger.py, un objetivo cuya ventana ya se rebasó
    # durante el corte se descarta con advertencia en vez de abrir una
    # ventana degenerada — así que ya no se esperan 7 ventanas necesariamente,
    # pero ninguna de las que sí se generan debería tener la muestra muy lejos
    # de su objetivo nominal.
    hubo_descarte = any(
        "se perdió" in a or "Se omiten además" in a for a in colector.advertencias
    )
    sin_ventanas_degeneradas = all(
        v["temp_apertura"] is not None and abs(v["temp_apertura"] - (v["objetivo"] + margen)) < 2.0
        for v in colector.ventanas
    )

    aprobado = orden_ok and hubo_descarte and sin_ventanas_degeneradas and colector.terminada
    detalle = (
        f"orden_ok={orden_ok} hubo_descarte={hubo_descarte} "
        f"sin_ventanas_degeneradas={sin_ventanas_degeneradas} "
        f"objetivos_medidos={[v['objetivo'] for v in colector.ventanas]}"
    )
    return ResultadoCaso("Caso 4 — Desconexión breve", aprobado, detalle)


def caso_5_desconexion_permanente() -> ResultadoCaso:
    print("\n=== Caso 5 — Desconexión permanente ===")
    original_adv = trigger.TIMEOUT_ADVERTENCIA_S
    original_abort = trigger.TIMEOUT_ABORTO_S
    trigger.TIMEOUT_ADVERTENCIA_S = 2.0
    trigger.TIMEOUT_ABORTO_S = 6.0
    try:
        doble = DobleTemperaturaSimulada(
            T0=62.0, T_amb=21.0, tau=10800.0, factor_compresion=100.0,
            fallo_real=(0.0, None), semilla=5,
        )
        tw = TriggerWorker(modo="temperatura", temp_worker=doble, t_inicial=60.0, t_final=30.0, paso=5.0)
        colector = ColectorSenales(tw, doble)
        t_ini = time.monotonic()
        tw.iniciar()
        duracion_real = time.monotonic() - t_ini
    finally:
        trigger.TIMEOUT_ADVERTENCIA_S = original_adv
        trigger.TIMEOUT_ABORTO_S = original_abort

    for a in colector.advertencias:
        print(f"  [advertencia] {a}")

    fin_emitida = any("Se termina la secuencia" in a for a in colector.advertencias)
    aprobado = fin_emitida and colector.terminada and duracion_real < 30.0
    detalle = (
        f"advertencia_fin_emitida={fin_emitida} secuencia_terminada={colector.terminada} "
        f"duracion_real={duracion_real:.1f}s"
    )
    return ResultadoCaso("Caso 5 — Desconexión permanente", aprobado, detalle)


def caso_6_rebote_termico() -> ResultadoCaso:
    print("\n=== Caso 6 — Rebote térmico en el umbral ===")
    T0, T_amb, tau, factor = 62.0, 21.0, 10800.0, 20.0
    margen = trigger.MARGEN_UMBRAL
    objetivo_rebote = 45.0
    duracion_rebote = 3.0
    magnitud_rebote = 0.15

    # La ventana de un objetivo abre con retraso variable respecto al t=0 del
    # doble — depende de las confirmaciones de _esperar_umbral y del jitter
    # real del scheduler, que varía de una corrida a otra. Precalcular el
    # instante del rebote desde t=0 (como se hacía antes) puede no solaparse
    # con la ventana real. En vez de eso, se arma el rebote reaccionando a la
    # señal iniciar_acumulacion del objetivo, usando su instante real de
    # apertura como referencia — el mismo criterio, pero anclado al evento
    # real en lugar de a una predicción analítica desde el arranque.
    duracion_esperada_real = duracion_esperada_sim(T_amb, tau, objetivo_rebote, margen) / factor

    doble = DobleTemperaturaSimulada(
        T0=T0, T_amb=T_amb, tau=tau, factor_compresion=factor, semilla=6,
    )
    tw = TriggerWorker(modo="temperatura", temp_worker=doble, t_inicial=60.0, t_final=30.0, paso=5.0)
    colector = ColectorSenales(tw, doble)

    rebote_armado = {"t_ini_real": None}

    def _armar_rebote():
        if colector._actual is not None and colector._actual["objetivo"] == objetivo_rebote:
            t_apertura_real = colector._actual["t_real_ini"]
            t_ini_real = t_apertura_real + duracion_esperada_real - duracion_rebote / 2
            doble.rebote_real = (t_ini_real, duracion_rebote, magnitud_rebote)
            rebote_armado["t_ini_real"] = t_ini_real

    tw.iniciar_acumulacion.connect(_armar_rebote)

    tw.iniciar()
    colector.imprimir_tabla()

    v = next((v for v in colector.ventanas if v["objetivo"] == objetivo_rebote), None)
    n = len(colector.ventanas)
    t_ini_real = rebote_armado["t_ini_real"]

    no_cierra_durante_rebote = (
        v is not None
        and t_ini_real is not None
        and v["t_real_fin"] >= t_ini_real + duracion_rebote
    )

    duraciones = {v2["objetivo"]: v2["duracion_sim"] for v2 in colector.ventanas}
    vecinos = [duraciones[t] for t in (40.0, 50.0) if t in duraciones]
    comparable_ok = False
    if v is not None and vecinos:
        promedio_vecinos = sum(vecinos) / len(vecinos)
        comparable_ok = promedio_vecinos > 0 and 0.1 <= (v["duracion_sim"] / promedio_vecinos) <= 10.0

    aprobado = n == 7 and no_cierra_durante_rebote and comparable_ok
    detalle = (
        f"ventanas={n} no_cierra_durante_rebote={no_cierra_durante_rebote} "
        f"duracion_objetivo={v['duracion_sim'] if v else None} vecinos={vecinos} comparable_ok={comparable_ok}"
    )
    return ResultadoCaso("Caso 6 — Rebote térmico en el umbral", aprobado, detalle)


def caso_7_bajo_todos_los_objetivos() -> ResultadoCaso:
    print("\n=== Caso 7 — Muestra por debajo de todos los objetivos ===")
    doble = DobleTemperaturaSimulada(
        T0=25.0, T_amb=21.0, tau=10800.0, factor_compresion=100.0, semilla=7
    )
    tw = TriggerWorker(modo="temperatura", temp_worker=doble, t_inicial=60.0, t_final=30.0, paso=5.0)
    colector = ColectorSenales(tw, doble)
    tw.iniciar()
    colector.imprimir_tabla()
    for a in colector.advertencias:
        print(f"  [advertencia] {a}")

    advertencia_ok = any(
        "por debajo de todos los objetivos" in a or "No hay puntos que medir" in a
        for a in colector.advertencias
    )
    sin_ventanas = len(colector.ventanas) == 0

    aprobado = advertencia_ok and sin_ventanas and colector.terminada
    detalle = f"advertencia_ok={advertencia_ok} ventanas={len(colector.ventanas)} terminada={colector.terminada}"
    return ResultadoCaso("Caso 7 — Muestra por debajo de todos los objetivos", aprobado, detalle)


def caso_8_perdida_por_reconexion() -> ResultadoCaso:
    print("\n=== Caso 8 — Objetivo perdido por reconexión durante la apertura ===")
    T0, T_amb, tau, factor = 62.0, 21.0, 10800.0, 150.0
    margen = trigger.MARGEN_UMBRAL
    objetivo_perdido = 45.0

    # Corte que empieza un poco antes de que la muestra cruce el umbral de
    # apertura de 45 (el trigger ya está esperando esa apertura) y termina
    # bastante después de que cruce su umbral de cierre — para cuando la
    # lectura se restablece, el objetivo ya quedó completamente rebasado.
    # El corte se mantiene dentro del hueco entre el cierre de 50 y la
    # apertura de 40, para no arrastrar objetivos vecinos.
    t_apertura_sim = t_sim_para_temp(T0, T_amb, tau, objetivo_perdido + margen)
    t_cierre_sim = t_sim_para_temp(T0, T_amb, tau, objetivo_perdido - margen)
    ini_sim = t_apertura_sim - 100.0
    fin_sim = t_cierre_sim + 560.0
    t_ini_real = ini_sim / factor
    t_fin_real = fin_sim / factor

    # El corte real (unos 5 s) es mucho más corto que TIMEOUT_ADVERTENCIA_S
    # por omisión (30 s): sin bajarlo, "advertido" nunca se activaría dentro
    # del hueco disponible entre objetivos vecinos, y el mecanismo de pérdida
    # por reconexión — que solo se evalúa al restablecerse la lectura tras
    # una interrupción advertida — no llegaría a ejercitarse.
    original_adv = trigger.TIMEOUT_ADVERTENCIA_S
    trigger.TIMEOUT_ADVERTENCIA_S = 2.0
    try:
        doble = DobleTemperaturaSimulada(
            T0=T0, T_amb=T_amb, tau=tau, factor_compresion=factor,
            fallo_real=(t_ini_real, t_fin_real), semilla=8,
        )
        tw = TriggerWorker(modo="temperatura", temp_worker=doble, t_inicial=60.0, t_final=30.0, paso=5.0)
        colector = ColectorSenales(tw, doble)
        tw.iniciar()
    finally:
        trigger.TIMEOUT_ADVERTENCIA_S = original_adv

    colector.imprimir_tabla()
    for a in colector.advertencias:
        print(f"  [advertencia] {a}")

    perdida_ok = any(
        f"El objetivo {objetivo_perdido:.1f}" in a and "se perdió" in a
        for a in colector.advertencias
    )
    sin_ventana_degenerada = not any(v["objetivo"] == objetivo_perdido for v in colector.ventanas)
    objetivos_medidos = [v["objetivo"] for v in colector.ventanas]
    resto_ok = objetivos_medidos == [60.0, 55.0, 50.0, 40.0, 35.0, 30.0]

    aprobado = perdida_ok and sin_ventana_degenerada and resto_ok and colector.terminada
    detalle = (
        f"perdida_ok={perdida_ok} sin_ventana_degenerada={sin_ventana_degenerada} "
        f"resto_ok={resto_ok} objetivos_medidos={objetivos_medidos}"
    )
    return ResultadoCaso(
        "Caso 8 — Objetivo perdido por reconexión durante la apertura", aprobado, detalle
    )


# ══════════════════════════════════════════════════════════════════════════
# Nivel dos: cadena completa
# ══════════════════════════════════════════════════════════════════════════


def nivel_dos_cadena_completa() -> ResultadoCaso:
    print("\n=== Nivel 2 — Cadena completa (Medicion + dobles + Almacenamiento real) ===")

    from app.almacenamiento.almacenamiento import Almacenamiento
    from app.medicion.medicion import Medicion

    T0, T_amb, tau, factor = 62.0, 21.0, 10800.0, 300.0

    with tempfile.TemporaryDirectory(prefix="barrido_simulado_") as tmp:
        doble_temp = DobleTemperaturaSimulada(
            T0=T0, T_amb=T_amb, tau=tau, factor_compresion=factor, semilla=42
        )
        doble_laser = DobleLaser()
        doble_modo_seguro = DobleModoSeguro()
        doble_oscil = DobleOsciloscopio()
        doble_monitor = DobleMonitor()

        store = Almacenamiento()
        if not store.nueva_sesion(nombre="nivel2", carpeta_base=Path(tmp)):
            return ResultadoCaso("Nivel 2 — Cadena completa", False, "No se pudo crear la sesión temporal.")

        medicion = Medicion(
            laser_ctrl=doble_laser,
            oscil_ctrl=doble_oscil,
            temp_worker=doble_temp,
            almacenamiento=store,
            modo_seguro=doble_modo_seguro,
            monitor=doble_monitor,
        )

        app = QCoreApplication.instance()
        estado = {"con_flag": None, "abortada": None, "timeout": False}

        def _on_ok(con_flag):
            estado["con_flag"] = con_flag
            app.quit()

        def _on_abortada(motivo):
            estado["abortada"] = motivo
            app.quit()

        def _on_advertencia(msg):
            print(f"  [advertencia] {msg}")

        def _on_timeout():
            estado["timeout"] = True
            app.quit()

        medicion.secuencia_ok.connect(_on_ok)
        medicion.secuencia_abortada.connect(_on_abortada)
        medicion.advertencia.connect(_on_advertencia)

        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(_on_timeout)
        timeout.start(10 * 60 * 1000)

        medicion.iniciar(modo="temperatura", t_inicial=60.0, t_final=30.0, paso=5.0)

        app.exec()
        timeout.stop()

        for th in (medicion._worker_thread, medicion._trigger_thread):
            if th is not None:
                th.wait(3000)

        if estado["timeout"]:
            return ResultadoCaso("Nivel 2 — Cadena completa", False, "Timeout esperando la secuencia.")
        if estado["abortada"] is not None:
            return ResultadoCaso(
                "Nivel 2 — Cadena completa", False, f"Secuencia abortada: {estado['abortada']}"
            )

        filas = store.cargar_csv()
        if not filas:
            return ResultadoCaso("Nivel 2 — Cadena completa", False, "No se pudo leer el CSV de la sesión.")

        n_filas = len(filas)
        pulsos_ok = all(float(f["pulsos_estimados"] or 0) > 0 for f in filas)
        temps = [float(f["temperatura"]) for f in filas]
        monotona_ok = all(temps[i] > temps[i + 1] for i in range(len(temps) - 1))

        store2 = Almacenamiento()
        abre_ok = store2.abrir_sesion(store.csv_path)

        print(
            f"  filas CSV: {n_filas}, pulsos_ok={pulsos_ok}, "
            f"temp_monotona={monotona_ok}, abrir_sesion_ok={abre_ok}"
        )

        aprobado = n_filas == 7 and pulsos_ok and monotona_ok and abre_ok
        detalle = f"filas={n_filas} pulsos_ok={pulsos_ok} monotona_ok={monotona_ok} abre_ok={abre_ok}"
        return ResultadoCaso("Nivel 2 — Cadena completa", aprobado, detalle)


# ══════════════════════════════════════════════════════════════════════════
# Orquestación
# ══════════════════════════════════════════════════════════════════════════


CASOS = {
    1: caso_1_barrido_nominal,
    2: caso_2_arranque_tibio,
    3: caso_3_ruido_elevado,
    4: caso_4_desconexion_breve,
    5: caso_5_desconexion_permanente,
    6: caso_6_rebote_termico,
    7: caso_7_bajo_todos_los_objetivos,
    8: caso_8_perdida_por_reconexion,
}


def imprimir_resumen(resultados: list[ResultadoCaso]):
    print("\n" + "=" * 70)
    print("RESUMEN")
    print("=" * 70)
    n_ok = 0
    for r in resultados:
        estado = "OK   " if r.aprobado else "FALLA"
        print(f"[{estado}] {r.nombre}")
        if r.detalle:
            print(f"          {r.detalle}")
        if r.aprobado:
            n_ok += 1
    print("-" * 70)
    print(f"{n_ok}/{len(resultados)} casos aprobados")


def main():
    parser = argparse.ArgumentParser(description="Simulador de barrido de temperatura.")
    parser.add_argument(
        "caso", nargs="?", type=int, default=None,
        help="Número de caso a correr (1-8). Sin argumento corre los 8 casos más el nivel dos.",
    )
    args = parser.parse_args()

    app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    resultados: list[ResultadoCaso] = []
    if args.caso is not None:
        fn = CASOS.get(args.caso)
        if fn is None:
            print(f"Caso inválido: {args.caso}. Use un número entre 1 y 7.")
            sys.exit(1)
        resultados.append(fn())
    else:
        for n in sorted(CASOS):
            resultados.append(CASOS[n]())
        resultados.append(nivel_dos_cadena_completa())

    imprimir_resumen(resultados)
    sys.exit(0 if all(r.aprobado for r in resultados) else 1)


if __name__ == "__main__":
    main()
