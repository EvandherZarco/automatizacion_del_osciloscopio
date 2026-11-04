"""
probar_testigo_adquisicion.py
Pruebas de regresión, sin hardware, del testigo ACQ:NUMACQ? en
OsciloscopioController (app/osciloscopio/control_osciloscopio.py).

El controlador es el real; el instrumento VXI-11 es un osciloscopio simulado
cuyo ACQ:NUMACQ? entrega una secuencia de lecturas guiada por la prueba (la
última se repite) y que registra en orden todo comando que recibe.

Casos:
    1. Lectura rancia tras RUN: el contador se lee ANTES del RUN, la lectura
       vieja que sigue al RUN se ignora y la captura, fresca, queda sin marca.
    2. Contador que no sube tras el reinicio: la captura se marca.
    3. Promedio incompleto en modo tiempo: la captura se marca.
    4. Exceso en modo temperatura (más de duración × FREC_DISPARO_HZ × 1.15):
       la captura se marca.
    5. Exceso en modo tiempo (NUMACQ > NUMAVG): la captura se marca.
    6. Cambio de escala entre RUN y STOP (modo temperatura): la captura se marca.
    7. Caída del contador a mitad de la ventana, vista por muestrear_numacq():
       la captura se marca.
    8. Reinicio que no se ve en TOPE_REINICIO_S: aviso en vivo y captura marcada.
    9. FREC_DISPARO_HZ vale 10.

Uso (CMD):
    venv\\Scripts\\python codigos_de_prueba\\ventana_ambos\\probar_testigo_adquisicion.py

Termina con código distinto de cero si el conjunto de casos aprobados no
coincide con el esperado.
"""

import struct
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _apoyo import app_qt, limpiar  # noqa: E402

from app.osciloscopio import control_osciloscopio as co  # noqa: E402
from app.osciloscopio.control_osciloscopio import OsciloscopioController  # noqa: E402

CASOS_ESPERADOS = {str(i) for i in range(1, 10)}

_PUNTOS = 4
_VENTANA_S = 10.0


def verificar(condicion, mensaje):
    if not condicion:
        raise AssertionError(mensaje)


def _curve_bytes(valores) -> bytes:
    datos = b"".join(struct.pack(">h", v) for v in valores)
    largo = str(len(datos)).encode()
    return b"#" + str(len(largo)).encode() + largo + datos + b"\n"


class _Osciloscopio:
    """Osciloscopio simulado: NUMACQ guiado y registro ordenado de comandos."""

    def __init__(self, numacq, modo="AVERAGE", numavg=10000, estado="1"):
        self.numacq = list(numacq)
        self.log: list[str] = []
        self.respuestas = {
            "WFMPRE:XINCR?": "1e-8",
            "WFMPRE:XZERO?": "0",
            "WFMPRE:PT_OFF?": "0",
            "WFMPRE:YMULT?": "1e-3",
            "WFMPRE:YOFF?": "0",
            "WFMPRE:YZERO?": "0",
            "WFMPRE:NR_PT?": str(_PUNTOS),
            "CH1:SCALE?": "0.1",
            "HORizontal:SCAle?": "1e-6",
            "ACQ:MODE?": modo,
            "ACQ:NUMAVG?": str(numavg),
            "ACQ:STATE?": estado,
            "*OPC?": "1",
        }
        self.cierres = 0

    def ask(self, cmd):
        self.log.append(f"A:{cmd}")
        if cmd == "ACQ:NUMACQ?":
            valor = self.numacq.pop(0) if len(self.numacq) > 1 else self.numacq[0]
            return str(valor)
        return self.respuestas[cmd]

    def write(self, cmd):
        self.log.append(f"W:{cmd}")

    def read_raw(self):
        return _curve_bytes([1, 2, 3, 4])

    def close(self):
        self.cierres += 1


class _Registro:
    def __init__(self, oscil):
        self.errores: list[str] = []
        oscil.error.connect(self.errores.append)


def _conectado(inst):
    oscil = OsciloscopioController()
    oscil._inst = inst
    oscil._connected = True
    oscil._nr_pt = _PUNTOS
    return oscil, _Registro(oscil)


def _rapido():
    """Acorta las esperas reales de la prueba; devuelve cómo restaurarlas."""
    guardado = (co.POLL_ADQUISICION_S, getattr(co, "TOPE_REINICIO_S", None))
    co.POLL_ADQUISICION_S = 0.001

    def restaurar():
        co.POLL_ADQUISICION_S = guardado[0]
        if guardado[1] is None:
            if hasattr(co, "TOPE_REINICIO_S"):
                del co.TOPE_REINICIO_S
        else:
            co.TOPE_REINICIO_S = guardado[1]

    return restaurar


def _fijar_duracion(oscil, segundos):
    """Hace que la ventana abierta por acq_run() parezca de `segundos` s."""
    ventana = getattr(oscil, "_ventana", None)
    verificar(ventana is not None, "acq_run no dejó una ventana abierta")
    ventana.t_inicio = time.monotonic() - segundos


def _numacq_antes_del_run(inst):
    """True si la primera lectura de NUMACQ precede al primer ACQ:STATE RUN."""
    primera_lectura = inst.log.index("A:ACQ:NUMACQ?")
    return primera_lectura < inst.log.index("W:ACQ:STATE RUN")


def _con_desc(captura, texto):
    verificar(captura is not None, "no se devolvió captura")
    verificar(captura.error_flag == 1, f"captura sin marca (error_desc={captura.error_desc!r})")
    verificar(texto in captura.error_desc.lower(),
              f"falta {texto!r} en error_desc: {captura.error_desc!r}")


# ── Casos ─────────────────────────────────────────────────────────────────────

def caso_1_lectura_rancia_tras_run():
    restaurar = _rapido()
    try:
        inst = _Osciloscopio([10000, 10000, 0, 100])
        oscil, _reg = _conectado(inst)
        verificar(oscil.acq_run(), "acq_run falló")
        _fijar_duracion(oscil, _VENTANA_S)
        captura = oscil.acq_stop_and_capture()
    finally:
        restaurar()
    verificar(_numacq_antes_del_run(inst), "NUMACQ no se leyó antes del RUN")
    verificar(captura is not None, "no se devolvió captura")
    verificar(captura.error_flag == 0,
              f"captura fresca marcada: {captura.error_desc!r}")
    print("     NUMACQ leído antes del RUN; 10000 rancio ignorado; reinicio a 0; captura sin marca")


def caso_2_contador_sin_subir():
    restaurar = _rapido()
    try:
        inst = _Osciloscopio([500, 0, 0])
        oscil, _reg = _conectado(inst)
        oscil.acq_run()
        _fijar_duracion(oscil, _VENTANA_S)
        captura = oscil.acq_stop_and_capture()
    finally:
        restaurar()
    verificar(_numacq_antes_del_run(inst), "NUMACQ no se leyó antes del RUN")
    _con_desc(captura, "sin adquisición nueva")
    print("     reinicio a 0 y contador en 0 al detener: marcada")


def caso_3_promedio_incompleto_modo_tiempo():
    restaurar = _rapido()
    try:
        inst = _Osciloscopio([500, 0, 60], numavg=100, estado="0")
        oscil, _reg = _conectado(inst)
        captura = oscil.capturar_modo_tiempo()
    finally:
        restaurar()
    verificar(_numacq_antes_del_run(inst), "NUMACQ no se leyó antes del RUN")
    _con_desc(captura, "promedio incompleto (60 de 100)")
    print("     60 de 100 adquisiciones tras el reinicio: marcada")


def caso_4_exceso_modo_temperatura():
    restaurar = _rapido()
    try:
        inst = _Osciloscopio([500, 0, 400])
        oscil, _reg = _conectado(inst)
        oscil.acq_run()
        _fijar_duracion(oscil, _VENTANA_S)
        captura = oscil.acq_stop_and_capture()
    finally:
        restaurar()
    _con_desc(captura, "exceso de adquisiciones")
    print(f"     400 adquisiciones en {_VENTANA_S:g} s (máximo 115): marcada")


def caso_5_exceso_modo_tiempo():
    restaurar = _rapido()
    try:
        inst = _Osciloscopio([500, 0, 150], numavg=100, estado="0")
        oscil, _reg = _conectado(inst)
        captura = oscil.capturar_modo_tiempo()
    finally:
        restaurar()
    _con_desc(captura, "exceso de adquisiciones (150 con numavg 100)")
    print("     NUMACQ 150 con NUMAVG 100: marcada")


def caso_6_cambio_de_escala_en_ventana():
    restaurar = _rapido()
    try:
        inst = _Osciloscopio([500, 0, 50])
        oscil, _reg = _conectado(inst)
        oscil.acq_run()
        _fijar_duracion(oscil, _VENTANA_S)
        inst.respuestas["CH1:SCALE?"] = "0.2"
        captura = oscil.acq_stop_and_capture()
    finally:
        restaurar()
    _con_desc(captura, "escala cambiada entre run y stop")
    print("     CH_SCALE 0.1 a 0.2 entre RUN y STOP: marcada")


def caso_7_caida_a_mitad_de_ventana():
    restaurar = _rapido()
    try:
        inst = _Osciloscopio([500, 0, 40, 3, 25])
        oscil, _reg = _conectado(inst)
        oscil.acq_run()
        oscil.muestrear_numacq()
        oscil.muestrear_numacq()
        _fijar_duracion(oscil, _VENTANA_S)
        captura = oscil.acq_stop_and_capture()
    finally:
        restaurar()
    _con_desc(captura, "cayó a mitad de la ventana")
    print("     muestras 40 y luego 3: caída vista; el conteo final (25) es coherente y aun así se marca")


def caso_8_reinicio_no_visto():
    restaurar = _rapido()
    try:
        co.TOPE_REINICIO_S = 0.05
        inst = _Osciloscopio([500])
        oscil, reg = _conectado(inst)
        ok = oscil.acq_run()
        _fijar_duracion(oscil, _VENTANA_S)
        captura = oscil.acq_stop_and_capture()
    finally:
        restaurar()
    verificar(ok, "acq_run devolvió False por un reinicio no visto")
    verificar(any("no mostró el reinicio" in e for e in reg.errores),
              f"sin aviso en vivo del reinicio no visto: {reg.errores}")
    _con_desc(captura, "no mostró el reinicio")
    print("     contador fijo en 500: aviso en vivo y captura marcada")


def caso_9_frecuencia_de_disparo():
    verificar(co.FREC_DISPARO_HZ == 10, f"FREC_DISPARO_HZ={co.FREC_DISPARO_HZ}, esperado 10")
    print("     FREC_DISPARO_HZ = 10")


CASOS = (
    ("1", "Lectura rancia tras RUN: base fresca, sin marca", caso_1_lectura_rancia_tras_run),
    ("2", "Contador sin subir: marca", caso_2_contador_sin_subir),
    ("3", "Promedio incompleto en modo tiempo: marca", caso_3_promedio_incompleto_modo_tiempo),
    ("4", "Exceso en modo temperatura: marca", caso_4_exceso_modo_temperatura),
    ("5", "Exceso en modo tiempo: marca", caso_5_exceso_modo_tiempo),
    ("6", "Cambio de escala entre RUN y STOP: marca", caso_6_cambio_de_escala_en_ventana),
    ("7", "Caída del contador a mitad de ventana: marca", caso_7_caida_a_mitad_de_ventana),
    ("8", "Reinicio no visto en el tope: aviso y marca", caso_8_reinicio_no_visto),
    ("9", "FREC_DISPARO_HZ = 10", caso_9_frecuencia_de_disparo),
)


def main():
    print("=" * 72)
    print("Testigo de adquisición — OsciloscopioController (sin hardware)")
    print("=" * 72)

    app = app_qt()
    aprobados = set()
    for clave, descripcion, funcion in CASOS:
        print(f"[{clave}] {descripcion}")
        try:
            funcion()
            app.processEvents()
        except Exception as exc:
            print(f"     FALLO: {type(exc).__name__}: {exc}")
            if not isinstance(exc, AssertionError):
                traceback.print_exc()
            continue
        aprobados.add(clave)

    print("-" * 72)
    faltantes = sorted(CASOS_ESPERADOS - aprobados, key=int)
    print(f"Aprobados : {sorted(aprobados, key=int)}")
    print(f"Esperados : {sorted(CASOS_ESPERADOS, key=int)}")
    if faltantes:
        print(f"Fallaron  : {faltantes}")
        print("RESULTADO: FALLO")
        return 1
    print("RESULTADO: TODOS LOS CASOS APROBADOS")
    return 0


if __name__ == "__main__":
    try:
        codigo = main()
    finally:
        limpiar()
    sys.exit(codigo)
