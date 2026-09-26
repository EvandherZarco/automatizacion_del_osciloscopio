"""
probar_grupo_a_ventana_ambos.py
Prueba automatizada, sin hardware, de las correcciones de Grupo A de la
auditoría de banderas de estado, en VentanaAmbos (app/gui/ventana_ambos.py):

  - El stop de emergencia y "Aplicar" parámetros del láser informan el
    resultado real de cada comando, no un éxito supuesto.
  - Tras start()/stop() y tras el modo seguro, el estado del láser se marca
    incierto y se relee el registro State real, en vez de asumir RUN/STOP.
  - _leer_estado_laser reactiva el vigía de inactividad cuando el cambio de
    RUN/STOP llega por el polling.
  - _sesion_activa sigue en vivo a Almacenamiento.activo.
  - "Abrir CSV…" queda deshabilitado durante una secuencia automática.
  - _sel_canal no asume el canal pedido si set_canal falla.

El último hallazgo de Grupo A (el stop de emergencia no libera la GUI hasta
que Medición confirma el fin) no se repite aquí: lo cubren los casos 1–5 de
probar_stop_emergencia_red_seguridad.py.

Casos:
    1. Stop de emergencia con los cuatro comandos enviados: log de éxito.
    2. Stop de emergencia con un comando fallido: el log lo nombra y no
       anuncia éxito.
    3. "Aplicar" con todos los parámetros aceptados: log de éxito.
    4. "Aplicar" con parámetros rechazados: el log los nombra y no anuncia
       éxito (incluye Burst length, que solo se envía fuera de Continuous).
    5. Iniciar láser: start() acepta pero State responde STOP → la GUI
       muestra STOP, no RUN.
    6. Iniciar láser: start() acepta y State responde RUN → RUN y vigía de
       inactividad armado.
    7. Detener láser: stop() acepta pero State no se puede leer → estado
       incierto ("?"), no STOP.
    8. Modo seguro: con State aún en RUN, la GUI muestra RUN (no asume
       STOP); sin láser conectado queda incierto.
    9. Polling de State: STOP→RUN arma el vigía de inactividad y RUN→STOP
       lo detiene, sin intervención del usuario.
   10. _sesion_activa refleja Almacenamiento.activo en vivo, incluida una
       sesión abierta por fuera de la ventana (reimportar).
   11. Iniciar una secuencia deshabilita "Abrir CSV…" y secuencia_ok lo
       vuelve a habilitar.
   12. set_canal falla: la selección vuelve al canal real del
       osciloscopio; si además está desconectado, no queda ninguno.
   13. set_canal acepta: la selección es la pedida y "Capturar" se
       habilita.

Uso:
    venv\\Scripts\\python codigos_de_prueba\\ventana_ambos\\probar_grupo_a_ventana_ambos.py

Imprime un resumen por caso y termina con código distinto de cero si el
conjunto de casos aprobados no coincide con el esperado.
"""

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _apoyo import APPDATA_TEMPORAL, CajasCapturadas, app_qt, crear_ventana_ambos, limpiar  # noqa: E402

from app.almacenamiento.almacenamiento import Almacenamiento  # noqa: E402

CASOS_ESPERADOS = {str(i) for i in range(1, 14)}

_ventana = None
_cajas: CajasCapturadas | None = None


def verificar(condicion, mensaje):
    if not condicion:
        raise AssertionError(mensaje)


# ── Dobles ────────────────────────────────────────────────────────────────────

class _LaserFalso:
    """Láser conectado cuyo registro State y resultados de comandos se fijan a mano."""

    def __init__(self, state="STOP", leer_ok=True):
        self.conectado = True
        self.state = state
        self.leer_ok = leer_ok
        self.acepta_start = True
        self.acepta_stop = True
        self.rechazados: set[str] = set()
        self.lecturas_state = 0
        self.enviados: list[str] = []

    def start(self):
        return self.acepta_start

    def stop(self):
        return self.acepta_stop

    def leer_estado(self):
        self.lecturas_state += 1
        return (True, self.state) if self.leer_ok else (False, "")

    def leer_parametros(self):
        return {"output_level": None, "eo_delay_us": None, "burst_mode": None}

    def _set(self, nombre):
        self.enviados.append(nombre)
        return nombre not in self.rechazados

    def set_output_level(self, _v):
        return self._set("output_level")

    def set_burst_mode(self, _v):
        return self._set("burst_mode")

    def set_burst_length(self, _v):
        return self._set("burst_length")

    def set_cooling_temp(self, _v):
        return self._set("cooling_temp")

    def set_eo_delay(self, _v):
        return self._set("eo_delay")


class _SafeFalso:
    def __init__(self, fallidos=()):
        self._fallidos = set(fallidos)

    def activar(self):
        return {k: k not in self._fallidos
                for k in ("stop", "eo_delay_3800", "e_off", "burst_cont")}


class _OscilCanal:
    def __init__(self, canal_real="CH1", acepta=False, conectado=True):
        self.canal = canal_real
        self.acepta = acepta
        self.conectado = conectado
        self.pedidos: list[str] = []

    def set_canal(self, canal):
        self.pedidos.append(canal)
        if self.acepta:
            self.canal = canal
        return self.acepta


class _TempConectado:
    def esta_conectado(self):
        return True


class _MedicionEspia:
    def __init__(self):
        self.llamadas_iniciar = 0

    def iniciar(self, **_kw):
        self.llamadas_iniciar += 1

    def detener(self):
        pass


class _Sustituir:
    """Reemplaza atributos de la ventana y los restaura al salir."""

    def __init__(self, **atributos):
        self._nuevos = atributos

    def __enter__(self):
        self._originales = {k: getattr(_ventana, k) for k in self._nuevos}
        for k, v in self._nuevos.items():
            setattr(_ventana, k, v)
        return self

    def __exit__(self, *_exc):
        for k, v in self._originales.items():
            setattr(_ventana, k, v)
        return False


def _log() -> str:
    return _ventana._lbl_log.text()


def _activo(btn) -> bool:
    return bool(btn.property("activo"))


# ── Casos ─────────────────────────────────────────────────────────────────────

def caso_1_stop_emergencia_exito():
    _ventana._secuencia_running = False
    with _Sustituir(_safe=_SafeFalso()):
        _ventana._on_stop_emergencia()
    verificar(_log() == "⚠ Stop emergencia — modo seguro activado", f"log: {_log()!r}")
    print(f"     log: {_log()!r}")


def caso_2_stop_emergencia_con_fallos():
    _ventana._secuencia_running = False
    with _Sustituir(_safe=_SafeFalso(fallidos=("e_off",))):
        _ventana._on_stop_emergencia()
    verificar("fallos" in _log() and "e_off" in _log(), f"el log no nombra el fallo: {_log()!r}")
    verificar(_log() != "⚠ Stop emergencia — modo seguro activado",
              "el log anuncia éxito pese a un comando fallido")
    print(f"     log: {_log()!r}")


def caso_3_aplicar_exito():
    laser = _LaserFalso()
    with _Sustituir(_laser=laser):
        _ventana._burst_sel = "Continuous"
        _ventana._on_aplicar_laser()
    verificar(_log() == "Parámetros del láser aplicados", f"log: {_log()!r}")
    verificar("burst_length" not in laser.enviados, "Burst length se envió en modo Continuous")
    print(f"     log: {_log()!r}, enviados={laser.enviados}")


def caso_4_aplicar_con_rechazos():
    laser = _LaserFalso()
    laser.rechazados = {"eo_delay", "burst_length"}
    with _Sustituir(_laser=laser):
        _ventana._burst_sel = "Burst"
        _ventana._on_aplicar_laser()
        _ventana._burst_sel = "Continuous"
    verificar("EO delay" in _log() and "Burst length" in _log(),
              f"el log no nombra los parámetros rechazados: {_log()!r}")
    verificar("Output level" not in _log(), f"el log nombra un parámetro aceptado: {_log()!r}")
    verificar(_log() != "Parámetros del láser aplicados", "el log anuncia éxito pese a rechazos")
    print(f"     log: {_log()!r}")


def caso_5_iniciar_no_asume_run():
    laser = _LaserFalso(state="STOP")
    with _Sustituir(_laser=laser):
        _ventana._fijar_estado_laser("STOP")
        _ventana._on_laser_iniciar()
    verificar(laser.lecturas_state >= 1, "no se leyó State tras start()")
    verificar(_ventana._laser_running is False, "la GUI asumió RUN con State=STOP")
    verificar(_ventana._laser_estado_txt == "STOP", f"estado mostrado: {_ventana._laser_estado_txt!r}")
    print("     start() aceptado, State=STOP → la GUI muestra STOP")


def caso_6_iniciar_confirmado_arma_vigia():
    laser = _LaserFalso(state="RUN")
    with _Sustituir(_laser=laser):
        _ventana._fijar_estado_laser("STOP")
        _ventana._secuencia_running = False
        _ventana._on_laser_iniciar()
        running = _ventana._laser_running
        vigia = _ventana._timer_inactividad.isActive()
        _ventana._timer_inactividad.stop()
        _ventana._fijar_estado_laser("STOP")
    verificar(running is True, "State=RUN no se reflejó como RUN")
    verificar(vigia, "el vigía de inactividad no quedó armado con el láser en RUN")
    print("     start() aceptado, State=RUN → RUN y vigía de inactividad armado")


def caso_7_detener_sin_lectura_queda_incierto():
    laser = _LaserFalso(state="RUN")
    with _Sustituir(_laser=laser):
        _ventana._fijar_estado_laser("RUN")
        laser.leer_ok = False
        _ventana._on_laser_detener()
        estado, incierto = _ventana._laser_estado_txt, _ventana._laser_incierto
        _ventana._fijar_estado_laser("STOP")
    verificar(laser.lecturas_state >= 1, "no se intentó leer State tras stop()")
    verificar(estado == "?" and incierto, f"estado={estado!r}, incierto={incierto}: se asumió STOP")
    print("     stop() aceptado, State ilegible → estado '?' (incierto)")


def caso_8_modo_seguro_no_asume_stop():
    laser = _LaserFalso(state="RUN")
    with _Sustituir(_laser=laser):
        _ventana._fijar_estado_laser("RUN")
        _ventana._on_modo_seguro(True, [])
        estado_conectado = _ventana._laser_estado_txt
        laser.conectado = False
        _ventana._fijar_estado_laser("RUN")
        _ventana._on_modo_seguro(True, [])
        estado_desconectado = _ventana._laser_estado_txt
        _ventana._timer_inactividad.stop()
        _ventana._fijar_estado_laser("STOP")
    verificar(estado_conectado == "RUN",
              f"con State=RUN tras modo seguro la GUI muestra {estado_conectado!r}")
    verificar(estado_desconectado == "?",
              f"sin láser conectado la GUI muestra {estado_desconectado!r}, esperado '?'")
    print("     modo seguro con State=RUN → RUN; sin láser conectado → '?'")


def caso_9_polling_arma_y_desarma_vigia():
    laser = _LaserFalso(state="STOP")
    with _Sustituir(_laser=laser):
        _ventana._secuencia_running = False
        _ventana._fijar_estado_laser("STOP")
        _ventana._timer_inactividad.stop()

        laser.state = "RUN"
        _ventana._leer_estado_laser()
        armado = _ventana._timer_inactividad.isActive()

        laser.state = "STOP"
        _ventana._leer_estado_laser()
        desarmado = not _ventana._timer_inactividad.isActive()
    verificar(armado, "STOP→RUN por polling no armó el vigía de inactividad")
    verificar(desarmado, "RUN→STOP por polling no detuvo el vigía de inactividad")
    print("     polling: STOP→RUN arma el vigía, RUN→STOP lo detiene")


def caso_10_sesion_activa_en_vivo():
    verificar(_ventana._store.activo is False, "el almacenamiento ya tenía sesión al empezar")
    verificar(_ventana._sesion_activa is False, "_sesion_activa=True sin sesión")

    otra = Almacenamiento()
    base = Path(tempfile.mkdtemp(prefix="sesion_", dir=APPDATA_TEMPORAL))
    verificar(otra.nueva_sesion(carpeta_base=base), "no se pudo crear la sesión a reimportar")
    verificar(_ventana._store.abrir_sesion(otra.csv_path), "abrir_sesion rechazó la sesión")

    verificar(_ventana._sesion_activa is True,
              "tras abrir una sesión por fuera de la ventana, _sesion_activa sigue en False")
    print("     sin sesión → False; sesión abierta desde Almacenamiento → True, sin tocar la ventana")


def caso_11_reimportar_bloqueado_durante_secuencia():
    medicion = _MedicionEspia()
    btn = _ventana._tab_viz._btn_reimportar
    laser = _LaserFalso(state="RUN")
    oscil_real = _ventana._oscil
    with _Sustituir(_laser=laser, _temp=_TempConectado(), _medicion=medicion):
        _ventana._laser_running = True
        oscil_real._connected = True
        _ventana._sel_modo_auto("temperatura")
        _ventana._spin_t_paso.setValue(1.0)
        btn.setEnabled(True)
        _cajas.limpiar()
        try:
            _ventana._intentar_iniciar_secuencia()
            durante = btn.isEnabled()
            _ventana._on_secuencia_ok(0)
            despues = btn.isEnabled()
        finally:
            oscil_real._connected = False
            _ventana._laser_running = False
            _ventana._secuencia_running = False
    verificar(medicion.llamadas_iniciar == 1,
              f"la secuencia no llegó a iniciarse (avisos: {_cajas.textos()})")
    verificar(durante is False, "'Abrir CSV…' siguió habilitado con la secuencia en curso")
    verificar(despues is True, "'Abrir CSV…' no se rehabilitó tras secuencia_ok")
    print("     durante la secuencia: deshabilitado; tras secuencia_ok: habilitado")


def caso_12_canal_fallido_refleja_el_real():
    with _Sustituir(_oscil=_OscilCanal(canal_real="CH1", acepta=False, conectado=True)):
        _ventana._canal_sel = "CH1"
        _ventana._sel_canal("CH2")
        conectado = (_ventana._canal_sel, _activo(_ventana._btn_p_ch1), _activo(_ventana._btn_p_ch2))

    with _Sustituir(_oscil=_OscilCanal(canal_real="CH1", acepta=False, conectado=False)):
        _ventana._canal_sel = "CH1"
        _ventana._sel_canal("CH2")
        desconectado = _ventana._canal_sel

    verificar(conectado == ("CH1", True, False),
              f"(canal_sel, CH1 activo, CH2 activo) = {conectado}, esperado ('CH1', True, False)")
    verificar(desconectado is None,
              f"desconectado, _canal_sel={desconectado!r}, esperado None")
    print("     set_canal falla: queda CH1 (el real); desconectado: ningún canal")


def caso_13_canal_aceptado():
    with _Sustituir(_oscil=_OscilCanal(canal_real="CH1", acepta=True, conectado=True)):
        _ventana._secuencia_running = False
        _ventana._btn_capturar.setEnabled(False)
        _ventana._sel_canal("CH2")
        resultado = (_ventana._canal_sel, _activo(_ventana._btn_p_ch1),
                     _activo(_ventana._btn_p_ch2), _ventana._btn_capturar.isEnabled())
        _ventana._canal_sel = None
        _ventana._btn_capturar.setEnabled(False)
    verificar(resultado == ("CH2", False, True, True),
              f"(canal_sel, CH1, CH2, Capturar) = {resultado}, esperado ('CH2', False, True, True)")
    print("     set_canal acepta: CH2 seleccionado y 'Capturar' habilitado")


CASOS = (
    ("1", "Stop de emergencia sin fallos: log de éxito", caso_1_stop_emergencia_exito),
    ("2", "Stop de emergencia con un comando fallido: el log lo nombra",
     caso_2_stop_emergencia_con_fallos),
    ("3", "Aplicar parámetros sin rechazos: log de éxito", caso_3_aplicar_exito),
    ("4", "Aplicar parámetros con rechazos: el log los nombra", caso_4_aplicar_con_rechazos),
    ("5", "Iniciar láser con State=STOP: no se asume RUN", caso_5_iniciar_no_asume_run),
    ("6", "Iniciar láser con State=RUN: RUN y vigía armado", caso_6_iniciar_confirmado_arma_vigia),
    ("7", "Detener láser con State ilegible: incierto, no STOP",
     caso_7_detener_sin_lectura_queda_incierto),
    ("8", "Modo seguro: se relee State, no se asume STOP", caso_8_modo_seguro_no_asume_stop),
    ("9", "Polling de State arma y desarma el vigía de inactividad",
     caso_9_polling_arma_y_desarma_vigia),
    ("10", "_sesion_activa sigue a Almacenamiento.activo en vivo", caso_10_sesion_activa_en_vivo),
    ("11", "'Abrir CSV…' bloqueado durante la secuencia", caso_11_reimportar_bloqueado_durante_secuencia),
    ("12", "set_canal falla: la selección refleja el canal real", caso_12_canal_fallido_refleja_el_real),
    ("13", "set_canal acepta: canal pedido y 'Capturar' habilitado", caso_13_canal_aceptado),
)


def main():
    global _ventana, _cajas

    print("=" * 72)
    print("Prueba de Grupo A — VentanaAmbos (sin hardware)")
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
