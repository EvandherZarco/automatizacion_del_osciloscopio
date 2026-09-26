"""
probar_modo_degradado.py
Prueba automatizada, sin hardware, de dos correcciones sobre la red de
seguridad del stop de emergencia (ver probar_stop_emergencia_red_seguridad.py):

  1. Validación de paso > 0 en modo temperatura, antes de iniciar la
     secuencia: con paso <= 0, TriggerWorker._generar_objetivos() puede
     entrar en un bucle infinito que nunca revisa _activo (reproducido por
     separado durante el desarrollo). Se rechaza desde la GUI, con el
     mismo criterio que ya usa la validación de intervalo mínimo en modo
     tiempo.

  2. Modo degradado (_modo_degradado): si el timer de respaldo de 150 s
     vence sin que nadie confirme el fin de la secuencia anterior, se
     verificó por separado (repro mínimo con QThread, fuera de esta
     prueba) que el hilo que causó el cuelgue sigue vivo — thread.quit()
     + thread.wait() no lo detiene. Por eso, además de liberar la GUI, se
     bloquea "Iniciar secuencia" de forma dura hasta reiniciar la
     aplicación, en los tres lugares que podían reactivar el botón, y
     "Volver" también bloquea (no solo avisa) en este estado.

Casos:
    1. paso<=0 en modo temperatura se rechaza antes de llamar a
       Medicion.iniciar().
    2. El timeout de respaldo activa el modo degradado y bloquea
       "Iniciar secuencia" (con tooltip explicando por qué).
    3. En modo degradado, _intentar_iniciar_secuencia se niega aunque el
       botón se hubiera podido reactivar por otro camino (defensa en
       profundidad).
    4. En modo degradado, ni _reset_ui_auto() ni un guardado manual
       exitoso reactivan "Iniciar secuencia".
    5. En modo degradado, "Volver" BLOQUEA de verdad — no llega a
       _cerrar_recursos() ni emite la señal volver, no basta con que
       aparezca un diálogo.
    6. Sin modo degradado, "Volver" sigue funcionando normal (no quedó
       roto el camino feliz).

Uso:
    venv\\Scripts\\python codigos_de_prueba\\ventana_ambos\\probar_modo_degradado.py

Imprime un resumen por caso y termina con código distinto de cero si el
conjunto de casos aprobados no coincide con el esperado.
"""

import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _apoyo import CajasCapturadas, app_qt, crear_ventana_ambos, limpiar  # noqa: E402

CASOS_ESPERADOS = {"1", "2", "3", "4", "5", "6"}

_ventana = None
_cajas: CajasCapturadas | None = None


def verificar(condicion, mensaje):
    if not condicion:
        raise AssertionError(mensaje)


class _MedicionEspia:
    def __init__(self):
        self.llamadas_iniciar = 0
        self.llamadas_detener = 0

    def iniciar(self, **_kw):
        self.llamadas_iniciar += 1

    def detener(self):
        self.llamadas_detener += 1


class _SafeFalso:
    def activar(self):
        return {"stop": True, "eo_delay_3800": True, "e_off": True, "burst_cont": True}


class _TempConectadoFalso:
    """Solo para el caso 1: hace falta que esta_conectado() sea True para
    que la validación de paso, y no la de ESP32 desconectado, sea la que
    efectivamente se ejerza (en este entorno sin hardware el ESP32 real
    nunca está conectado)."""

    def esta_conectado(self):
        return True


@dataclass
class _CapturaFalsa:
    wfmpre: dict
    raw_data: object
    tiempo: object = None
    voltaje: object = None
    error_flag: int = 0
    error_desc: str = ""


class _TempFalsoCaptura:
    def consultar(self):
        return 21.0, [True, True, True, True], True

    def consultar_sensores(self):
        return [21.0] * 4, [False] * 4, True

    @property
    def puerto(self):
        return "FAKE"


class _LaserFalsoParams:
    conectado = True

    def leer_parametros(self):
        return {"output_level": None, "eo_delay_us": None, "burst_mode": None}


class _StoreFalso:
    activo = True
    reabierta = False
    session_id = "s"

    def guardar(self, _paquete):
        return "s_m0001"


class _MonitorFalso:
    error_flag = False
    dispositivos_con_error = []


def _forzar_modo_degradado():
    """Lleva la ventana al modo degradado por el camino real: stop de
    emergencia con secuencia activa, seguido del vencimiento del timer de
    respaldo (simulado, sin esperar los 150 s reales)."""
    _ventana._medicion = _MedicionEspia()
    _ventana._safe = _SafeFalso()
    _ventana._secuencia_running = True
    _ventana._on_stop_emergencia()
    _ventana._on_timeout_detencion_secuencia()
    verificar(_ventana._modo_degradado is True, "no se pudo preparar el modo degradado para la prueba")


# ── Casos ─────────────────────────────────────────────────────────────────────

def caso_1_paso_cero_se_rechaza():
    medicion = _MedicionEspia()
    _ventana._medicion = medicion
    _ventana._laser._connected = True
    _ventana._laser_running = True
    _ventana._oscil._connected = True
    _ventana._temp_real = _ventana._temp
    _ventana._temp = _TempConectadoFalso()
    _ventana._sel_modo_auto("temperatura")
    _ventana._spin_t_paso.setValue(0.0)

    _cajas.limpiar()
    _ventana._intentar_iniciar_secuencia()

    verificar(any("realizable" in t.lower() for t in _cajas.textos("warning")),
              "paso=0 no mostró 'Configuración no realizable'")
    verificar(medicion.llamadas_iniciar == 0,
              "paso=0 llegó a llamar Medicion.iniciar()")

    _ventana._spin_t_paso.setValue(1.0)
    _ventana._temp = _ventana._temp_real
    _ventana._laser._connected = False
    _ventana._oscil._connected = False
    _ventana._secuencia_running = False
    print("     paso=0 rechazado antes de iniciar la secuencia; Medicion.iniciar() nunca se llamó")


def caso_2_timeout_activa_modo_degradado():
    _forzar_modo_degradado()

    verificar(not _ventana._btn_iniciar_seq.isEnabled(),
              "'Iniciar secuencia' sigue habilitado en modo degradado")
    tooltip = _ventana._btn_iniciar_seq.toolTip().lower()
    verificar("reinici" in tooltip, f"el tooltip no explica que hay que reiniciar: {tooltip!r}")
    print("     modo_degradado=True, 'Iniciar secuencia' deshabilitado con tooltip explicativo")


def caso_3_intentar_iniciar_se_niega_en_profundidad():
    medicion = _MedicionEspia()
    _ventana._medicion = medicion
    _cajas.limpiar()

    _ventana._intentar_iniciar_secuencia()

    verificar(any("reinici" in t.lower() for t in _cajas.textos("critical")),
              "en modo degradado, _intentar_iniciar_secuencia no mostró el diálogo crítico esperado")
    verificar(medicion.llamadas_iniciar == 0,
              "en modo degradado, Medicion.iniciar() se llegó a invocar")
    print("     _intentar_iniciar_secuencia se niega aunque se la llame directamente")


def caso_4_nada_reactiva_el_boton():
    _ventana._reset_ui_auto()
    verificar(not _ventana._btn_iniciar_seq.isEnabled(),
              "_reset_ui_auto reactivó 'Iniciar secuencia' en modo degradado")

    _ventana._temp_r = _ventana._temp
    _ventana._laser_r = _ventana._laser
    _ventana._store_r = _ventana._store
    _ventana._monitor_r = _ventana._monitor
    _ventana._temp = _TempFalsoCaptura()
    _ventana._laser = _LaserFalsoParams()
    _ventana._store = _StoreFalso()
    _ventana._monitor = _MonitorFalso()

    captura = _CapturaFalsa(
        wfmpre={"XINCR": 1, "XZERO": 0, "PT_OFF": 0, "YMULT": 1, "YOFF": 0, "YZERO": 0, "NR_PT": 4},
        raw_data=np.array([1, 2, 3, 4]),
        tiempo=np.array([0.0, 1.0, 2.0, 3.0]),
        voltaje=np.array([0.1, 0.2, 0.3, 0.4]),
    )
    _ventana._on_captura_terminada(captura, None)
    _ventana._on_guardar_manual()

    verificar(not _ventana._btn_iniciar_seq.isEnabled(),
              "un guardado manual exitoso reactivó 'Iniciar secuencia' en modo degradado")

    _ventana._temp = _ventana._temp_r
    _ventana._laser = _ventana._laser_r
    _ventana._store = _ventana._store_r
    _ventana._monitor = _ventana._monitor_r
    print("     ni _reset_ui_auto() ni un guardado manual exitoso reactivan el botón")


def caso_5_volver_bloquea_de_verdad():
    volver_emitido = []
    _ventana.volver.connect(lambda: volver_emitido.append(True))
    _ventana._laser_running = False
    _ventana._secuencia_running = False
    _ventana._safe = _SafeFalso()
    _ventana._medicion = _MedicionEspia()

    verificar(_ventana._cerrado is False, "_cerrado ya estaba en True antes de este caso")
    _cajas.limpiar()

    _ventana._on_volver()

    verificar(any("reinici" in t.lower() for t in _cajas.textos("critical")),
              "'Volver' en modo degradado no mostró el diálogo crítico esperado")
    verificar(any("bloquead" in t.lower() for t in _cajas.textos("critical")),
              "el diálogo no dice explícitamente que 'Volver' queda bloqueado")
    verificar(_ventana._cerrado is False,
              "_on_volver llegó a _cerrar_recursos() pese al modo degradado "
              "(un solo clic en el diálogo dejaría volver a la bienvenida)")
    verificar(len(volver_emitido) == 0,
              "la señal volver se emitió pese al modo degradado")
    print("     'Volver' en modo degradado NO llega a _cerrar_recursos() ni emite volver")


def caso_6_volver_normal_sigue_funcionando():
    _ventana._modo_degradado = False
    _ventana._on_volver()
    verificar(_ventana._cerrado is True,
              "sin modo degradado, 'Volver' ya no ejecuta _cerrar_recursos()")
    print("     sin modo degradado, 'Volver' sigue cerrando la ventana normalmente")


CASOS = (
    ("1", "paso<=0 en modo temperatura se rechaza (nunca llama Medicion.iniciar())",
     caso_1_paso_cero_se_rechaza),
    ("2", "El timeout de respaldo activa modo degradado y bloquea 'Iniciar secuencia'",
     caso_2_timeout_activa_modo_degradado),
    ("3", "_intentar_iniciar_secuencia se niega en modo degradado (defensa en profundidad)",
     caso_3_intentar_iniciar_se_niega_en_profundidad),
    ("4", "Ni _reset_ui_auto ni guardar manual reactivan el botón en modo degradado",
     caso_4_nada_reactiva_el_boton),
    ("5", "'Volver' bloquea de verdad en modo degradado (no solo avisa)",
     caso_5_volver_bloquea_de_verdad),
    ("6", "Sin modo degradado, 'Volver' sigue funcionando normal",
     caso_6_volver_normal_sigue_funcionando),
)


def main():
    global _ventana, _cajas

    print("=" * 72)
    print("Prueba de validación de paso y modo degradado (sin hardware)")
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
    faltantes = sorted(CASOS_ESPERADOS - aprobados)
    print(f"Aprobados : {sorted(aprobados)}")
    print(f"Esperados : {sorted(CASOS_ESPERADOS)}")
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
