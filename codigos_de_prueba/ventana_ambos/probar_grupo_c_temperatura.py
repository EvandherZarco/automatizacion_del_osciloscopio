"""
probar_grupo_c_temperatura.py
Prueba automatizada, sin hardware, de las correcciones de Grupo C de la
auditoría de banderas de estado (ESP32 / temperatura):

  - El hilo de TempWorker se rinde ante una racha de tramas inválidas, no
    solo ante silencio real.
  - TempWorker.error queda conectado al log de la GUI.
  - Un sensor DS18B20 ausente activa error_flag=1, en captura manual
    (VentanaAmbos._on_guardar_manual) y automática
    (MedicionWorker._procesar_captura).

El bucle de TempWorker.iniciar() es el real; solo se reemplazan la apertura
del puerto, la espera de la primera trama y la lectura de líneas, y se
acorta TIMEOUT_SILENCIO a 0.3 s para no esperar los 5 s reales.

Casos:
    1. Solo tramas inválidas: el hilo se rinde (error + desconectado) al
       vencer TIMEOUT_SILENCIO y cierra el puerto.
    2. Solo tramas válidas durante más que TIMEOUT_SILENCIO: no se rinde.
    3. Tramas válidas intercaladas con basura, sin huecos mayores que
       TIMEOUT_SILENCIO: no se rinde.
    4. Silencio real (sin líneas): se sigue rindiendo como antes.
    5. TempWorker.error llega al log de la ventana.
    6. Guardado manual con un sensor ausente: fila con error_flag=1 que lo
       nombra.
    7. Guardado manual con los cuatro sensores presentes: fila limpia.
    8. Captura automática con un sensor ausente: paquete con error_flag=1
       que lo nombra.
    9. Captura automática con los cuatro sensores presentes: paquete limpio.

Uso:
    venv\\Scripts\\python codigos_de_prueba\\ventana_ambos\\probar_grupo_c_temperatura.py

Imprime un resumen por caso y termina con código distinto de cero si el
conjunto de casos aprobados no coincide con el esperado.
"""

import csv
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _apoyo import APPDATA_TEMPORAL, CajasCapturadas, app_qt, crear_ventana_ambos, limpiar  # noqa: E402

from app.medicion.medicion import MedicionWorker  # noqa: E402
from app.temperatura import temperatura  # noqa: E402
from app.temperatura.temperatura import TempWorker  # noqa: E402

CASOS_ESPERADOS = {str(i) for i in range(1, 10)}

SILENCIO_PRUEBA_S = 0.3
TOPE_BUCLE_S = 3.0
TRAMA_VALIDA = "21.50,21.25,21.75,21.50,21.50"

_ventana = None
_cajas: CajasCapturadas | None = None


def verificar(condicion, mensaje):
    if not condicion:
        raise AssertionError(mensaje)


# ── TempWorker con el puerto reemplazado ──────────────────────────────────────

class _Corrida:
    """Ejecuta TempWorker.iniciar() en este hilo con una fuente de líneas dada."""

    def __init__(self, fuente, duracion_max_s=TOPE_BUCLE_S):
        self.worker = TempWorker()
        self.fuente = fuente
        self.duracion_max_s = duracion_max_s
        self.errores: list[str] = []
        self.desconexiones = 0
        self.triggers = 0
        self.cierres = 0
        self.agotado_por_tope = False

        w = self.worker
        w._abrir_puerto = lambda: True
        w._esperar_primer_dato = lambda: True
        w._cerrar_puerto = self._cerrar
        w._leer_linea = self._leer
        w.error.connect(self.errores.append)
        w.desconectado.connect(self._desconectado)
        w.trigger.connect(self._trigger)

    def _cerrar(self):
        self.cierres += 1

    def _desconectado(self):
        self.desconexiones += 1

    def _trigger(self, _t):
        self.triggers += 1

    def _leer(self):
        transcurrido = time.monotonic() - self._t0
        if transcurrido > self.duracion_max_s:
            self.agotado_por_tope = True
            self.worker._activo = False
            return None
        time.sleep(0.01)
        return self.fuente(transcurrido)

    def correr(self) -> float:
        original = temperatura.TIMEOUT_SILENCIO
        temperatura.TIMEOUT_SILENCIO = SILENCIO_PRUEBA_S
        try:
            self._t0 = time.monotonic()
            self.worker.iniciar()
            return time.monotonic() - self._t0
        finally:
            temperatura.TIMEOUT_SILENCIO = original


# ── Dobles para captura manual y automática ───────────────────────────────────

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


class _TempSensores:
    def __init__(self, lecturas):
        self._lecturas = lecturas

    def consultar(self):
        return 21.5, [v is not None for v in self._lecturas], True

    def consultar_sensores(self):
        return list(self._lecturas), [False] * 4, True

    @property
    def puerto(self):
        return "FAKE"


class _LaserParams:
    conectado = True

    def leer_parametros(self):
        return {"output_level": "E Adjust", "eo_delay_us": 200, "burst_mode": "Continuous"}


class _MonitorSinError:
    error_flag = False
    dispositivos_con_error: list = []


class _StoreEspia:
    def __init__(self):
        self.paquetes = []

    def guardar(self, paquete):
        self.paquetes.append(paquete)
        return f"s_m{len(self.paquetes):04d}"


def _guardar_manual(lecturas) -> dict:
    """Captura + Guardar por el camino real de la ventana; devuelve la fila escrita."""
    originales = (_ventana._temp, _ventana._laser, _ventana._monitor)
    _ventana._temp = _TempSensores(lecturas)
    _ventana._laser = _LaserParams()
    _ventana._monitor = _MonitorSinError()
    try:
        if not _ventana._store.activo:
            verificar(_ventana._store.nueva_sesion(carpeta_base=Path(APPDATA_TEMPORAL)),
                      "no se pudo crear la sesión temporal")
        previas = len(_filas())
        _ventana._on_captura_terminada(_captura(), None)
        _ventana._on_guardar_manual()
    finally:
        _ventana._temp, _ventana._laser, _ventana._monitor = originales
    filas = _filas()
    verificar(len(filas) == previas + 1,
              f"Guardar escribió {len(filas) - previas} filas, esperada 1 "
              f"(diálogos mostrados: {_cajas.textos()})")
    return filas[-1]


def _filas() -> list[dict]:
    with open(_ventana._store.csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _procesar_automatico(lecturas):
    store = _StoreEspia()
    worker = MedicionWorker(
        modo="tiempo",
        laser_ctrl=_LaserParams(),
        temp_worker=_TempSensores(lecturas),
        almacenamiento=store,
    )
    worker._activo = True
    worker._procesar_captura(_captura())
    verificar(len(store.paquetes) == 1, f"se guardaron {len(store.paquetes)} paquetes, esperado 1")
    return store.paquetes[0]


# ── Casos ─────────────────────────────────────────────────────────────────────

def caso_1_basura_se_rinde():
    corrida = _Corrida(lambda _t: "\x00\xff#garbage,,")
    duracion = corrida.correr()

    verificar(not corrida.agotado_por_tope,
              f"el hilo siguió girando {TOPE_BUCLE_S} s con tramas inválidas sin rendirse")
    verificar(corrida.desconexiones == 1, f"desconectado emitido {corrida.desconexiones} veces")
    verificar(any("sin tramas válidas" in e for e in corrida.errores),
              f"el error no explica la causa: {corrida.errores}")
    verificar(corrida.cierres == 1, "el puerto no se cerró al rendirse")
    verificar(corrida.triggers == 0, "una trama inválida emitió trigger")
    print(f"     se rindió a los {duracion:.2f} s (TIMEOUT_SILENCIO={SILENCIO_PRUEBA_S} s) y cerró el puerto")


def caso_2_validas_no_se_rinde():
    corrida = _Corrida(lambda _t: TRAMA_VALIDA, duracion_max_s=4 * SILENCIO_PRUEBA_S)
    corrida.correr()

    verificar(corrida.desconexiones == 0, "se rindió con tramas válidas")
    verificar(corrida.agotado_por_tope, "el bucle terminó antes del tope sin motivo")
    verificar(corrida.triggers > 10, f"solo {corrida.triggers} triggers con tramas válidas")
    temp, sensores, fresca = corrida.worker.consultar()
    verificar(fresca and temp == 21.5 and all(sensores),
              f"lectura registrada incorrecta: temp={temp}, sensores={sensores}, fresca={fresca}")
    print(f"     {4 * SILENCIO_PRUEBA_S:.1f} s de tramas válidas: {corrida.triggers} triggers, sin rendirse")


def caso_3_validas_intercaladas_no_se_rinde():
    def fuente(t):
        return TRAMA_VALIDA if int(t / 0.1) % 2 == 0 else "basura"

    corrida = _Corrida(fuente, duracion_max_s=4 * SILENCIO_PRUEBA_S)
    corrida.correr()

    verificar(corrida.desconexiones == 0,
              "se rindió aunque llegaban tramas válidas cada 0.2 s")
    verificar(corrida.triggers > 0, "ninguna trama válida se registró")
    print(f"     válidas cada ~0.2 s entre basura: {corrida.triggers} triggers, sin rendirse")


def caso_4_silencio_se_rinde():
    corrida = _Corrida(lambda _t: None)
    corrida.correr()

    verificar(not corrida.agotado_por_tope, "el hilo no se rindió ante silencio real")
    verificar(corrida.desconexiones == 1, f"desconectado emitido {corrida.desconexiones} veces")
    print("     silencio real: se rinde igual que antes")


def caso_5_error_llega_al_log():
    _ventana._set_log("")
    _ventana._temp.error.emit("ESP32 en COMX sin tramas válidas por 5 s.")
    app_qt().processEvents()

    texto = _ventana._lbl_log.text()
    verificar("sin tramas válidas" in texto, f"el log de la ventana muestra {texto!r}")
    print(f"     log: {texto!r}")


def caso_6_manual_sensor_ausente():
    fila = _guardar_manual([21.5, None, 21.5, 21.5])

    verificar(fila["error_flag"] == "1", f"error_flag={fila['error_flag']!r} con S2 ausente")
    verificar("ausente" in fila["error_desc"] and "[2]" in fila["error_desc"],
              f"error_desc no nombra el sensor ausente: {fila['error_desc']!r}")
    verificar(fila["t_s2"] == "", f"t_s2={fila['t_s2']!r}, esperado vacío")
    print(f"     fila manual: error_flag=1, error_desc={fila['error_desc']!r}")


def caso_7_manual_sensores_completos():
    fila = _guardar_manual([21.5, 21.25, 21.75, 21.5])

    verificar(fila["error_flag"] == "0",
              f"error_flag={fila['error_flag']!r} con los cuatro sensores: {fila['error_desc']!r}")
    verificar(fila["error_desc"] == "", f"error_desc={fila['error_desc']!r}")
    print("     fila manual con S1–S4 presentes: error_flag=0")


def caso_8_automatico_sensor_ausente():
    paquete = _procesar_automatico([21.5, 21.5, None, None])

    verificar(paquete.error_flag == 1, f"error_flag={paquete.error_flag} con S3/S4 ausentes")
    verificar("ausente" in paquete.error_desc and "[3, 4]" in paquete.error_desc,
              f"error_desc no nombra los sensores ausentes: {paquete.error_desc!r}")
    print(f"     paquete automático: error_flag=1, error_desc={paquete.error_desc!r}")


def caso_9_automatico_sensores_completos():
    paquete = _procesar_automatico([21.5, 21.25, 21.75, 21.5])

    verificar(paquete.error_flag == 0,
              f"error_flag={paquete.error_flag} con los cuatro sensores: {paquete.error_desc!r}")
    print("     paquete automático con S1–S4 presentes: error_flag=0")


CASOS = (
    ("1", "Solo tramas inválidas: TempWorker se rinde y cierra el puerto", caso_1_basura_se_rinde),
    ("2", "Solo tramas válidas: TempWorker no se rinde", caso_2_validas_no_se_rinde),
    ("3", "Válidas intercaladas con basura: no se rinde", caso_3_validas_intercaladas_no_se_rinde),
    ("4", "Silencio real: se sigue rindiendo", caso_4_silencio_se_rinde),
    ("5", "TempWorker.error llega al log de la ventana", caso_5_error_llega_al_log),
    ("6", "Manual con sensor ausente → error_flag=1", caso_6_manual_sensor_ausente),
    ("7", "Manual con S1–S4 presentes → error_flag=0", caso_7_manual_sensores_completos),
    ("8", "Automático con sensor ausente → error_flag=1", caso_8_automatico_sensor_ausente),
    ("9", "Automático con S1–S4 presentes → error_flag=0", caso_9_automatico_sensores_completos),
)


def main():
    global _ventana, _cajas

    print("=" * 72)
    print("Prueba de Grupo C — ESP32 / temperatura (sin hardware)")
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
