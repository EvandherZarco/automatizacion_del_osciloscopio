"""
probar_grupo_b_osciloscopio.py
Prueba automatizada, sin hardware, de las correcciones de Grupo B de la
auditoría de banderas de estado, todas en OsciloscopioController
(app/osciloscopio/control_osciloscopio.py):

  - Agotar los reintentos leyendo WFMPRE o CURVE baja la bandera de
    conexión (_emit_conn_error), no queda como advertencia de captura.
  - _emit_conn_error cierra y suelta _inst: el enlace muerto no se sigue
    usando.
  - estimar_tiempo_captura relee WFMPRE:NR_PT? en vivo en vez de usar el
    valor cacheado al conectar.
  - desconectar() restaura ACQ:MODE/NUMAVG previos a una secuencia, como ya
    hacía reanudar_adquisicion().

El controlador es el real; solo el instrumento VXI-11 se reemplaza por un
objeto que responde a SCPI desde un diccionario y registra lo que recibe.

Casos:
    1. WFMPRE falla en todos los reintentos: conexión caída (LED rojo,
       conectado=False), no advertencia de captura (LED amarillo).
    2. CURVE falla en todos los reintentos: conexión caída, igual que 1.
    3. WFMPRE falla una sola vez y luego responde: la captura sale y la
       conexión sigue viva (los reintentos siguen sirviendo).
    4. Tras un error de conexión, _inst queda en None, el enlace se cerró
       una vez y un comando posterior (set_canal) no le escribe.
    5. estimar_tiempo_captura usa el NR_PT leído en ese momento y actualiza
       la caché.
    6. Si releer NR_PT falla, estimar_tiempo_captura usa la caché y no
       baja la conexión.
    7. desconectar() con una secuencia pendiente restaura ACQ:MODE y
       NUMAVG antes de cerrar, en ese orden.
    8. desconectar() sin nada que restaurar no envía comandos ACQ.
    9. Si la restauración falla, desconectar() igual cierra y suelta el
       enlace.
   10. reanudar_adquisicion() sigue restaurando tras mover la lógica a
       _restaurar_adquisicion_previa().

Uso:
    venv\\Scripts\\python codigos_de_prueba\\ventana_ambos\\probar_grupo_b_osciloscopio.py

Imprime un resumen por caso y termina con código distinto de cero si el
conjunto de casos aprobados no coincide con el esperado.
"""

import struct
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _apoyo import app_qt, limpiar  # noqa: E402

from app.osciloscopio import control_osciloscopio as co  # noqa: E402
from app.osciloscopio.control_osciloscopio import OsciloscopioController  # noqa: E402

CASOS_ESPERADOS = {str(i) for i in range(1, 11)}

_PUNTOS = 4


def verificar(condicion, mensaje):
    if not condicion:
        raise AssertionError(mensaje)


def _curve_bytes(valores) -> bytes:
    datos = b"".join(struct.pack(">h", v) for v in valores)
    largo = str(len(datos)).encode()
    return b"#" + str(len(largo)).encode() + largo + datos + b"\n"


class _InstrumentoScpi:
    """Instrumento VXI-11 falso: responde desde un diccionario y registra todo."""

    def __init__(self):
        self.respuestas = {
            "WFMPRE:XINCR?": "1e-8",
            "WFMPRE:XZERO?": "0",
            "WFMPRE:PT_OFF?": "0",
            "WFMPRE:YMULT?": "1e-3",
            "WFMPRE:YOFF?": "0",
            "WFMPRE:YZERO?": "0",
            "WFMPRE:NR_PT?": str(_PUNTOS),
            "CH1:SCALE?": "0.1",
            "CH2:SCALE?": "0.1",
            "HORizontal:SCAle?": "1e-6",
            "ACQ:MODE?": "SAMPLE",
            "ACQ:NUMAVG?": "16",
            "ACQ:NUMACQ?": "20",
            "ACQ:STATE?": "1",
            "*OPC?": "1",
        }
        self.fallos_ask: dict[str, int] = {}
        self.fallos_read_raw = 0
        self.fallar_write = False
        self.curve = _curve_bytes([1, 2, 3, 4])
        self.asks: list[str] = []
        self.writes: list[str] = []
        self.cierres = 0

    def ask(self, cmd):
        self.asks.append(cmd)
        pendientes = self.fallos_ask.get(cmd, 0)
        if pendientes:
            self.fallos_ask[cmd] = pendientes - 1
            raise TimeoutError(f"sin respuesta a {cmd}")
        return self.respuestas[cmd]

    def write(self, cmd):
        if self.fallar_write:
            raise TimeoutError(f"sin respuesta a {cmd}")
        self.writes.append(cmd)

    def read_raw(self):
        if self.fallos_read_raw:
            self.fallos_read_raw -= 1
            raise TimeoutError("sin respuesta a CURVE?")
        return self.curve

    def close(self):
        self.cierres += 1


class _Registro:
    def __init__(self, oscil: OsciloscopioController):
        self.rojo = 0
        self.amarillo = 0
        self.errores: list[str] = []
        oscil.led_rojo.connect(self._rojo)
        oscil.led_amarillo.connect(self._amarillo)
        oscil.error.connect(self.errores.append)

    def _rojo(self):
        self.rojo += 1

    def _amarillo(self):
        self.amarillo += 1


def _conectado(inst=None):
    """Controlador real en estado 'conectado' sobre un instrumento falso."""
    oscil = OsciloscopioController()
    inst = inst or _InstrumentoScpi()
    oscil._inst = inst
    oscil._connected = True
    oscil._nr_pt = _PUNTOS
    return oscil, inst, _Registro(oscil)


# ── Casos ─────────────────────────────────────────────────────────────────────

def caso_1_wfmpre_agotado_es_error_de_conexion():
    oscil, inst, reg = _conectado()
    inst.fallos_ask["WFMPRE:XINCR?"] = co.MAX_REINTENTOS

    captura = oscil.acq_stop_and_capture()

    verificar(captura is None, "con WFMPRE caído se devolvió una captura")
    verificar(inst.asks.count("WFMPRE:XINCR?") == co.MAX_REINTENTOS,
              f"se esperaban {co.MAX_REINTENTOS} intentos de WFMPRE, "
              f"hubo {inst.asks.count('WFMPRE:XINCR?')}")
    verificar(oscil.conectado is False, "la bandera de conexión siguió en True")
    verificar(reg.rojo == 1, f"LED rojo emitido {reg.rojo} veces, esperado 1")
    verificar(reg.amarillo == 0, "se emitió además una advertencia de captura (LED amarillo)")
    verificar(any("WFMPRE" in e for e in reg.errores), f"el error no menciona WFMPRE: {reg.errores}")
    print(f"     {co.MAX_REINTENTOS} intentos, conectado=False, LED rojo, sin LED amarillo")


def caso_2_curve_agotado_es_error_de_conexion():
    oscil, inst, reg = _conectado()
    inst.fallos_read_raw = co.MAX_REINTENTOS

    captura = oscil.acq_stop_and_capture()

    verificar(captura is None, "con CURVE caído se devolvió una captura")
    verificar(inst.writes.count("CURVE?") == co.MAX_REINTENTOS,
              f"se esperaban {co.MAX_REINTENTOS} CURVE?, hubo {inst.writes.count('CURVE?')}")
    verificar(oscil.conectado is False, "la bandera de conexión siguió en True")
    verificar(reg.rojo == 1, f"LED rojo emitido {reg.rojo} veces, esperado 1")
    verificar(reg.amarillo == 0, "se emitió además una advertencia de captura (LED amarillo)")
    verificar(any("CURVE" in e for e in reg.errores), f"el error no menciona CURVE: {reg.errores}")
    print(f"     {co.MAX_REINTENTOS} CURVE?, conectado=False, LED rojo, sin LED amarillo")


def caso_3_fallo_transitorio_no_baja_conexion():
    oscil, inst, reg = _conectado()
    inst.fallos_ask["WFMPRE:XINCR?"] = 1
    oscil._numacq_inicio = 10

    captura = oscil.acq_stop_and_capture()

    verificar(captura is not None, "un solo fallo de WFMPRE hizo perder la captura")
    verificar(len(captura.raw_data) == _PUNTOS, f"curva de {len(captura.raw_data)} puntos")
    verificar(oscil.conectado is True, "un fallo recuperado bajó la conexión")
    verificar(reg.rojo == 0, "un fallo recuperado encendió el LED rojo")
    verificar(captura.error_flag == 0, f"captura marcada sin motivo: {captura.error_desc}")
    print("     un fallo recuperado en el 2.º intento: captura válida, conexión intacta")


def caso_4_error_de_conexion_suelta_el_enlace():
    oscil, inst, _reg = _conectado()
    inst.fallos_ask["WFMPRE:XINCR?"] = co.MAX_REINTENTOS
    oscil.acq_stop_and_capture()

    verificar(oscil._inst is None, "_inst sigue apuntando al enlace muerto")
    verificar(inst.cierres == 1, f"close() llamado {inst.cierres} veces, esperado 1")

    writes_previos = len(inst.writes)
    oscil.set_canal("CH2")
    verificar(len(inst.writes) == writes_previos,
              f"set_canal escribió al enlace muerto: {inst.writes[writes_previos:]}")
    print("     _inst=None, close() una vez, set_canal ya no toca el enlace muerto")


def caso_5_estimacion_relee_nr_pt():
    oscil, inst, _reg = _conectado()
    oscil._nr_pt = 1_000
    inst.respuestas["WFMPRE:NR_PT?"] = "500000"

    estimado = oscil.estimar_tiempo_captura(100)

    t_prom = co.math.ceil((100 / co.FREC_DISPARO_HZ) / co.POLL_INTERVAL_S) * co.POLL_INTERVAL_S
    esperado = t_prom + (500_000 * 2) / co.TRANSFER_BYTES_POR_S + co.OVERHEAD_TRANSFERENCIA_S
    verificar("WFMPRE:NR_PT?" in inst.asks, "no se consultó NR_PT al estimar")
    verificar(abs(estimado - esperado) < 1e-9,
              f"estimado {estimado:.3f} s, esperado {esperado:.3f} s con NR_PT=500000")
    verificar(oscil._nr_pt == 500_000, f"la caché quedó en {oscil._nr_pt}")
    print(f"     NR_PT releído (1000 → 500000): estimado {estimado:.2f} s, caché actualizada")


def caso_6_estimacion_con_nr_pt_fallido_usa_cache():
    oscil, inst, reg = _conectado()
    oscil._nr_pt = 1_000
    inst.fallos_ask["WFMPRE:NR_PT?"] = 1

    estimado = oscil.estimar_tiempo_captura(100)

    t_prom = co.math.ceil((100 / co.FREC_DISPARO_HZ) / co.POLL_INTERVAL_S) * co.POLL_INTERVAL_S
    esperado = t_prom + (1_000 * 2) / co.TRANSFER_BYTES_POR_S + co.OVERHEAD_TRANSFERENCIA_S
    verificar(abs(estimado - esperado) < 1e-9,
              f"estimado {estimado:.3f} s, esperado {esperado:.3f} s con la caché (1000)")
    verificar(oscil.conectado is True, "un fallo al releer NR_PT bajó la conexión")
    verificar(reg.rojo == 0, "un fallo al releer NR_PT encendió el LED rojo")
    print("     NR_PT ilegible: se usa la caché (1000), conexión intacta")


def caso_7_desconectar_restaura_adquisicion():
    oscil, inst, _reg = _conectado()
    oscil._adquisicion_previa = ("SAMPLE", 16)

    oscil.desconectar()

    esperado = ["ACQ:STATE STOP", "ACQ:MODE SAMPLE", "ACQ:NUMAVG 16"]
    verificar(inst.writes == esperado, f"comandos enviados {inst.writes}, esperados {esperado}")
    verificar(inst.cierres == 1, f"close() llamado {inst.cierres} veces")
    verificar(oscil._adquisicion_previa is None, "la adquisición previa no se consumió")
    verificar(oscil._inst is None and oscil.conectado is False, "no quedó desconectado")
    print(f"     antes de cerrar: {', '.join(esperado)}")


def caso_8_desconectar_sin_nada_que_restaurar():
    oscil, inst, _reg = _conectado()

    oscil.desconectar()

    verificar(inst.writes == [], f"se enviaron comandos sin nada que restaurar: {inst.writes}")
    verificar(inst.cierres == 1, f"close() llamado {inst.cierres} veces")
    print("     sin secuencia previa: ningún comando ACQ, solo close()")


def caso_9_restauracion_fallida_igual_cierra():
    oscil, inst, reg = _conectado()
    oscil._adquisicion_previa = ("SAMPLE", 16)
    inst.fallar_write = True

    oscil.desconectar()

    verificar(inst.cierres == 1, f"close() llamado {inst.cierres} veces tras fallar la restauración")
    verificar(oscil._inst is None and oscil.conectado is False,
              "un fallo al restaurar impidió desconectar")
    verificar(any("restaurar" in e for e in reg.errores),
              f"el fallo de restauración no se avisó: {reg.errores}")
    print("     restauración fallida: se avisa y el enlace se cierra igual")


def caso_10_reanudar_sigue_restaurando():
    oscil, inst, _reg = _conectado()
    oscil._adquisicion_previa = ("HIRES", 8)

    ok = oscil.reanudar_adquisicion()

    verificar(ok is True, "reanudar_adquisicion devolvió False con ACQ:STATE? = 1")
    verificar(inst.writes[:3] == ["ACQ:STATE STOP", "ACQ:MODE HIRES", "ACQ:NUMAVG 8"],
              f"no restauró el modo previo: {inst.writes}")
    verificar(inst.writes[-1] == "ACQ:STATE RUN", f"no quedó en RUN: {inst.writes}")
    verificar(oscil._adquisicion_previa is None, "la adquisición previa no se consumió")
    print("     restaura HIRES/8 y deja el osciloscopio en RUN")


CASOS = (
    ("1", "WFMPRE agotado → error de conexión, no advertencia de captura",
     caso_1_wfmpre_agotado_es_error_de_conexion),
    ("2", "CURVE agotado → error de conexión, no advertencia de captura",
     caso_2_curve_agotado_es_error_de_conexion),
    ("3", "Un fallo transitorio de WFMPRE no baja la conexión",
     caso_3_fallo_transitorio_no_baja_conexion),
    ("4", "El error de conexión cierra y suelta _inst",
     caso_4_error_de_conexion_suelta_el_enlace),
    ("5", "estimar_tiempo_captura relee NR_PT en vivo",
     caso_5_estimacion_relee_nr_pt),
    ("6", "NR_PT ilegible: se usa la caché sin bajar la conexión",
     caso_6_estimacion_con_nr_pt_fallido_usa_cache),
    ("7", "desconectar() restaura ACQ:MODE/NUMAVG previos",
     caso_7_desconectar_restaura_adquisicion),
    ("8", "desconectar() sin secuencia previa no envía comandos ACQ",
     caso_8_desconectar_sin_nada_que_restaurar),
    ("9", "Restauración fallida: desconectar() cierra igual",
     caso_9_restauracion_fallida_igual_cierra),
    ("10", "reanudar_adquisicion() sigue restaurando",
     caso_10_reanudar_sigue_restaurando),
)


def main():
    print("=" * 72)
    print("Prueba de Grupo B — OsciloscopioController (sin hardware)")
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
