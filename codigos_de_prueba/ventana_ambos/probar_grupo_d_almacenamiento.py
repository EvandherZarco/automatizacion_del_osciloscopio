"""
probar_grupo_d_almacenamiento.py
Prueba automatizada, sin hardware, de las correcciones de Grupo D de la
auditoría de banderas de estado (almacenamiento):

  - Almacenamiento.guardado_err y MedicionWorker.error llegan a algún lado
    (log de la ventana y Medicion.advertencia, respectivamente).
  - Si np.save falla, la fila se escribe igual pero con error_flag=1 y la
    causa en error_desc; solo un fallo al escribir el CSV devuelve None.
  - La fila manual toma temperatura y parámetros del láser en el instante
    de la captura, no al pulsar Guardar.

Almacenamiento es el real, escribiendo en un directorio temporal (nunca en
datos/). Medicion es la real, con sus QThreads, sobre controladores falsos.

Casos:
    1. Almacenamiento.guardado_err llega al log de la ventana.
    2. MedicionWorker.error llega a Medicion.advertencia (con hilos reales:
       un láser que no arranca aborta la secuencia y el motivo se ve).
    3. Guardado normal: devuelve el id, fila con error_flag=0 y .npy
       idéntico a raw_data.
    4. np.save falla: devuelve el id igualmente, fila con error_flag=1,
       error_desc con la causa, y guardado_err emitido.
    5. np.save falla sobre un paquete que ya traía error_desc: se conservan
       ambas descripciones.
    6. El CSV no se puede escribir: devuelve None, guardado_err emitido y
       guardado_ok no.
    7. Temperatura y parámetros del láser cambian entre Capturar y Guardar:
       la fila conserva los del instante de la captura.
    8. Una segunda captura antes de Guardar reemplaza esos metadatos: la
       fila lleva los de la última captura, no los de la primera.

Uso:
    venv\\Scripts\\python codigos_de_prueba\\ventana_ambos\\probar_grupo_d_almacenamiento.py

Imprime un resumen por caso y termina con código distinto de cero si el
conjunto de casos aprobados no coincide con el esperado.
"""

import csv
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _apoyo import APPDATA_TEMPORAL, CajasCapturadas, app_qt, crear_ventana_ambos, limpiar  # noqa: E402

from app.almacenamiento import almacenamiento as alm  # noqa: E402
from app.almacenamiento.almacenamiento import Almacenamiento, PaqueteMedicion  # noqa: E402
from app.medicion.medicion import Medicion  # noqa: E402

CASOS_ESPERADOS = {str(i) for i in range(1, 9)}

_ventana = None
_cajas: CajasCapturadas | None = None


def verificar(condicion, mensaje):
    if not condicion:
        raise AssertionError(mensaje)


# ── Almacenamiento real en directorio temporal ────────────────────────────────

class _Senales:
    def __init__(self, store: Almacenamiento):
        self.ok: list[str] = []
        self.err: list[str] = []
        store.guardado_ok.connect(self.ok.append)
        store.guardado_err.connect(self.err.append)


def _store_temporal():
    store = Almacenamiento()
    base = Path(tempfile.mkdtemp(prefix="sesion_", dir=APPDATA_TEMPORAL))
    verificar(store.nueva_sesion(carpeta_base=base), "no se pudo crear la sesión temporal")
    return store, _Senales(store)


def _paquete(error_flag=0, error_desc=""):
    return PaqueteMedicion(
        timestamp="2026-09-26T12:00:00",
        temperatura=21.5,
        modo="manual",
        wfmpre={"XINCR": 1e-8, "XZERO": 0.0, "PT_OFF": 0, "YMULT": 1e-3,
                "YOFF": 0.0, "YZERO": 0.0, "NR_PT": 4},
        raw_data=np.array([1, -2, 3, -4], dtype=np.int16),
        error_flag=error_flag,
        error_desc=error_desc,
    )


def _filas(store: Almacenamiento) -> list[dict]:
    with open(store.csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _fila_nueva(store: Almacenamiento, previas: int) -> dict:
    filas = _filas(store)
    verificar(len(filas) == previas + 1,
              f"Guardar escribió {len(filas) - previas} filas, esperada 1 "
              f"(diálogos mostrados: {_cajas.textos()})")
    return filas[-1]


class _NpSaveFallido:
    """Hace fallar np.save solo dentro del bloque with."""

    def __enter__(self):
        self._original = alm.np.save

        def _falla(*_a, **_kw):
            raise OSError("disco lleno (simulado)")

        alm.np.save = _falla
        return self

    def __exit__(self, *_exc):
        alm.np.save = self._original
        return False


# ── Dobles para Medicion y para la captura manual ────────────────────────────

class _LaserQueNoArranca:
    conectado = True

    def start(self):
        return False

    def leer_parametros(self):
        return {}


class _OscilPasivo:
    def cancelar_espera(self):
        pass

    def reanudar_adquisicion(self):
        return True


class _SafeFalso:
    def activar(self):
        return {"stop": True, "eo_delay_3800": True, "e_off": True, "burst_cont": True}


class _MonitorPasivo:
    error_flag = False
    dispositivos_con_error: list = []

    def pausar_pings(self):
        pass

    def reanudar_pings(self):
        pass


@dataclass
class _CapturaFalsa:
    wfmpre: dict
    raw_data: object
    tiempo: object = None
    voltaje: object = None
    error_flag: int = 0
    error_desc: str = ""


def _captura():
    return _CapturaFalsa(
        wfmpre={"XINCR": 1e-8, "XZERO": 0.0, "PT_OFF": 0, "YMULT": 1e-3,
                "YOFF": 0.0, "YZERO": 0.0, "NR_PT": 4},
        raw_data=np.array([1, 2, 3, 4], dtype=np.int16),
        tiempo=np.array([0.0, 1.0, 2.0, 3.0]),
        voltaje=np.array([0.1, 0.2, 0.3, 0.4]),
    )


class _TempVariable:
    def __init__(self, temp):
        self.temp = temp

    def consultar(self):
        return self.temp, [True] * 4, True

    def consultar_sensores(self):
        return [self.temp] * 4, [False] * 4, True

    @property
    def puerto(self):
        return "FAKE"


class _LaserVariable:
    conectado = True

    def __init__(self, eo_delay_us):
        self.eo_delay_us = eo_delay_us

    def leer_parametros(self):
        return {"output_level": "E Adjust", "eo_delay_us": self.eo_delay_us,
                "burst_mode": "Continuous"}


class _Sustitutos:
    """Cambia temp/láser/monitor de la ventana y los restaura al salir."""

    def __init__(self, temp, laser):
        self.temp, self.laser = temp, laser

    def __enter__(self):
        v = _ventana
        self._originales = (v._temp, v._laser, v._monitor)
        v._temp, v._laser, v._monitor = self.temp, self.laser, _MonitorPasivo()
        if not v._store.activo:
            base = Path(tempfile.mkdtemp(prefix="sesion_", dir=APPDATA_TEMPORAL))
            verificar(v._store.nueva_sesion(carpeta_base=base), "no se pudo crear la sesión temporal")
        return self

    def __exit__(self, *_exc):
        _ventana._temp, _ventana._laser, _ventana._monitor = self._originales
        return False


# ── Casos ─────────────────────────────────────────────────────────────────────

def caso_1_guardado_err_llega_al_log():
    _ventana._set_log("")
    _ventana._store.guardado_err.emit("Error al escribir CSV (s_m0001): permiso denegado")
    app_qt().processEvents()

    texto = _ventana._lbl_log.text()
    verificar("Error al escribir CSV" in texto, f"el log de la ventana muestra {texto!r}")
    print(f"     log: {texto!r}")


def caso_2_error_del_worker_llega_a_advertencia():
    app = app_qt()
    medicion = Medicion(_LaserQueNoArranca(), _OscilPasivo(), None,
                        Almacenamiento(), _SafeFalso(), _MonitorPasivo())
    advertencias: list[str] = []
    abortos: list[str] = []
    medicion.advertencia.connect(advertencias.append)
    medicion.secuencia_abortada.connect(abortos.append)

    medicion.iniciar(modo="tiempo", intervalo=60.0, n_mediciones=1)
    limite = time.monotonic() + 5.0
    while not abortos and time.monotonic() < limite:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    medicion._limpiar_threads()

    verificar(abortos, "la secuencia no se abortó en 5 s con un láser que no arranca")
    verificar(any("láser" in a.lower() for a in advertencias),
              f"el error del worker no llegó a Medicion.advertencia: {advertencias}")
    print(f"     advertencia recibida desde el hilo del worker: {advertencias[0]!r}")


def caso_3_guardado_normal():
    store, sen = _store_temporal()
    paquete = _paquete()

    mid = store.guardar(paquete)

    verificar(mid is not None, "el guardado normal devolvió None")
    fila = _fila_nueva(store, 0)
    verificar(fila["medicion_id"] == mid, f"medicion_id={fila['medicion_id']!r}, esperado {mid!r}")
    verificar(fila["error_flag"] == "0", f"error_flag={fila['error_flag']!r}")
    npy = np.load(store.csv_path.parent / f"{mid}.npy")
    verificar(np.array_equal(npy, paquete.raw_data), f".npy distinto de raw_data: {npy}")
    verificar(sen.ok == [mid] and sen.err == [], f"señales: ok={sen.ok}, err={sen.err}")
    print(f"     {mid}: error_flag=0, .npy idéntico, guardado_ok emitido")


def caso_4_npy_fallido_marca_la_fila():
    store, sen = _store_temporal()

    with _NpSaveFallido():
        mid = store.guardar(_paquete())

    verificar(mid is not None, "un fallo del .npy devolvió None (la fila del CSV sí existe)")
    fila = _fila_nueva(store, 0)
    verificar(fila["error_flag"] == "1", f"error_flag={fila['error_flag']!r} sin .npy")
    verificar(".npy" in fila["error_desc"] and "disco lleno" in fila["error_desc"],
              f"error_desc no explica la causa: {fila['error_desc']!r}")
    verificar(not (store.csv_path.parent / f"{mid}.npy").exists(), "apareció un .npy pese al fallo")
    verificar(any(".npy" in e for e in sen.err), f"guardado_err no se emitió: {sen.err}")
    print(f"     fila marcada: error_desc={fila['error_desc']!r}")


def caso_5_npy_fallido_conserva_descripcion_previa():
    store, _sen = _store_temporal()

    with _NpSaveFallido():
        store.guardar(_paquete(error_flag=1, error_desc="ESP32 sin respuesta (COM3)"))

    desc = _fila_nueva(store, 0)["error_desc"]
    verificar(desc.startswith("ESP32 sin respuesta (COM3); "),
              f"se perdió la descripción previa: {desc!r}")
    verificar(".npy" in desc, f"falta la causa del .npy: {desc!r}")
    print(f"     error_desc={desc!r}")


def caso_6_csv_fallido_devuelve_none():
    store, sen = _store_temporal()
    store._csv_path = store.csv_path.parent

    mid = store.guardar(_paquete())

    verificar(mid is None, f"un CSV no escribible devolvió {mid!r}")
    verificar(any("CSV" in e for e in sen.err), f"guardado_err no se emitió: {sen.err}")
    verificar(sen.ok == [], f"guardado_ok se emitió pese al fallo: {sen.ok}")
    print("     CSV no escribible: None, guardado_err emitido, guardado_ok no")


def caso_7_metadatos_del_instante_de_captura():
    temp = _TempVariable(21.5)
    laser = _LaserVariable(200)
    with _Sustitutos(temp, laser):
        previas = len(_filas(_ventana._store))
        _ventana._on_captura_terminada(_captura(), None)
        temp.temp = 30.0
        laser.eo_delay_us = 999
        _ventana._on_guardar_manual()

    fila = _fila_nueva(_ventana._store, previas)
    verificar(float(fila["temperatura"]) == 21.5,
              f"temperatura={fila['temperatura']!r}: se tomó la del instante de Guardar")
    verificar(fila["eo_delay_us"] == "200",
              f"eo_delay_us={fila['eo_delay_us']!r}: se tomó el del instante de Guardar")
    verificar(float(fila["t_s1"]) == 21.5, f"t_s1={fila['t_s1']!r}")
    print("     captura a 21.5 °C / EO 200 µs, Guardar a 30 °C / EO 999 µs → fila con 21.5 / 200")


def caso_8_segunda_captura_reemplaza_metadatos():
    temp = _TempVariable(21.5)
    laser = _LaserVariable(200)
    with _Sustitutos(temp, laser):
        previas = len(_filas(_ventana._store))
        _ventana._on_captura_terminada(_captura(), None)
        temp.temp = 25.0
        laser.eo_delay_us = 300
        _ventana._on_captura_terminada(_captura(), None)
        temp.temp = 30.0
        laser.eo_delay_us = 999
        _ventana._on_guardar_manual()

    fila = _fila_nueva(_ventana._store, previas)
    verificar(float(fila["temperatura"]) == 25.0,
              f"temperatura={fila['temperatura']!r}, esperada la de la 2.ª captura (25.0)")
    verificar(fila["eo_delay_us"] == "300",
              f"eo_delay_us={fila['eo_delay_us']!r}, esperado el de la 2.ª captura (300)")
    print("     dos capturas (21.5 → 25.0 °C) y Guardar a 30 °C → fila con 25.0 / 300")


CASOS = (
    ("1", "guardado_err llega al log de la ventana", caso_1_guardado_err_llega_al_log),
    ("2", "MedicionWorker.error llega a Medicion.advertencia (hilos reales)",
     caso_2_error_del_worker_llega_a_advertencia),
    ("3", "Guardado normal: id, error_flag=0 y .npy idéntico", caso_3_guardado_normal),
    ("4", "np.save falla: fila con error_flag=1 y la causa", caso_4_npy_fallido_marca_la_fila),
    ("5", "np.save falla: se conserva la descripción previa",
     caso_5_npy_fallido_conserva_descripcion_previa),
    ("6", "CSV no escribible: devuelve None", caso_6_csv_fallido_devuelve_none),
    ("7", "La fila manual usa los metadatos del instante de captura",
     caso_7_metadatos_del_instante_de_captura),
    ("8", "Una segunda captura reemplaza los metadatos",
     caso_8_segunda_captura_reemplaza_metadatos),
)


def main():
    global _ventana, _cajas

    print("=" * 72)
    print("Prueba de Grupo D — almacenamiento (sin hardware)")
    print("=" * 72)

    _cajas = CajasCapturadas()
    _ventana = crear_ventana_ambos(_cajas)
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
