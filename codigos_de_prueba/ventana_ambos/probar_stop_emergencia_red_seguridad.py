"""
probar_stop_emergencia_red_seguridad.py
Prueba automatizada, sin hardware, de la red de seguridad que se agregó a
VentanaAmbos._on_stop_emergencia(): tras Grupo A (auditoría de banderas de
estado), el stop de emergencia dejó de liberar la GUI de inmediato cuando
hay una secuencia automática corriendo — espera a que Medición confirme el
fin real con secuencia_ok/secuencia_abortada, para no dejar "Iniciar
secuencia" habilitado mientras el worker anterior todavía vive.

Eso abre una pregunta: ¿qué pasa si esa confirmación nunca llega (el
worker/trigger queda bloqueado)? _timer_fallback_detencion (150 s, ver
TIMEOUT_DETENCION_SECUENCIA_MS en ventana_ambos.py) es la respuesta: libera
la GUI de todos modos si el plazo vence sin confirmación.

Casos:
    1. Con secuencia activa, el stop de emergencia arranca el timer de
       respaldo y NO libera la GUI de inmediato.
    2. Si secuencia_ok llega antes del plazo, cancela el timer y libera la
       GUI por el camino normal.
    3. Si nadie confirma, al vencer el plazo (simulado, no se espera el
       tiempo real) libera la GUI igual, con una advertencia VISIBLE
       (QMessageBox), no solo una línea de log.
    4. secuencia_abortada también cancela el timer; si el timer dispara
       tarde, después de una resolución normal, no hace nada de más.
    5. Sin secuencia activa, el stop de emergencia no arranca el timer y
       libera la GUI de inmediato, como antes de este cambio.

Uso:
    venv\\Scripts\\python codigos_de_prueba\\ventana_ambos\\probar_stop_emergencia_red_seguridad.py

Imprime un resumen por caso y termina con código distinto de cero si el
conjunto de casos aprobados no coincide con el esperado.
"""

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _apoyo import CajasCapturadas, app_qt, crear_ventana_ambos, limpiar  # noqa: E402

from app.gui.ventana_ambos import TIMEOUT_DETENCION_SECUENCIA_MS  # noqa: E402

CASOS_ESPERADOS = {"1", "2", "3", "4", "5"}

_ventana = None
_cajas: CajasCapturadas | None = None


def verificar(condicion, mensaje):
    if not condicion:
        raise AssertionError(mensaje)


class _MedicionFalsa:
    """Espía de Medicion: no arma hilos reales, solo cuenta llamadas a detener()."""

    def __init__(self):
        self.llamadas_detener = 0

    def detener(self):
        self.llamadas_detener += 1


class _SafeFalso:
    """Espía de ModoSeguro: activar() siempre "sale bien" salvo que se pida lo contrario."""

    def __init__(self, resultados=None):
        self._resultados = resultados or {
            "stop": True, "eo_delay_3800": True, "e_off": True, "burst_cont": True,
        }

    def activar(self):
        return dict(self._resultados)


def _preparar_stop_emergencia(secuencia_running: bool):
    ventana = _ventana
    medicion = _MedicionFalsa()
    safe = _SafeFalso()
    ventana._medicion = medicion
    ventana._safe = safe
    ventana._secuencia_running = secuencia_running
    ventana._btn_iniciar_seq.setEnabled(False)
    ventana._tab_viz.fijar_reimportacion_habilitada(not secuencia_running)
    return medicion, safe


# ── Casos ─────────────────────────────────────────────────────────────────────

def caso_1_arranca_timer_y_no_libera():
    medicion, _safe = _preparar_stop_emergencia(secuencia_running=True)

    verificar(not _ventana._timer_fallback_detencion.isActive(),
              "el timer de respaldo ya estaba activo antes del stop")

    _ventana._on_stop_emergencia()

    verificar(medicion.llamadas_detener == 1, "Medicion.detener() no se invocó")
    verificar(_ventana._timer_fallback_detencion.isActive(),
              "el timer de respaldo no arrancó tras el stop con secuencia activa")
    verificar(_ventana._timer_fallback_detencion.interval() == TIMEOUT_DETENCION_SECUENCIA_MS,
              f"intervalo = {_ventana._timer_fallback_detencion.interval()} ms, "
              f"esperado {TIMEOUT_DETENCION_SECUENCIA_MS} ms")
    verificar(_ventana._secuencia_running is True,
              "_secuencia_running se puso en False de inmediato (debería esperar la confirmación)")
    verificar(not _ventana._btn_iniciar_seq.isEnabled(),
              "'Iniciar secuencia' se reactivó antes de tiempo")
    verificar(not _ventana._tab_viz._btn_reimportar.isEnabled(),
              "'Abrir CSV…' se reactivó antes de tiempo")
    print(f"     timer activo, intervalo={TIMEOUT_DETENCION_SECUENCIA_MS} ms, "
          f"_secuencia_running sigue True")


def caso_2_confirmacion_normal_cancela_timer():
    _ventana._on_secuencia_ok(0)

    verificar(not _ventana._timer_fallback_detencion.isActive(),
              "secuencia_ok no canceló el timer de respaldo")
    verificar(_ventana._secuencia_running is False,
              "_secuencia_running no pasó a False tras secuencia_ok")
    verificar(_ventana._btn_iniciar_seq.isEnabled() == _ventana._sesion_activa,
              "'Iniciar secuencia' no volvió al estado correcto tras secuencia_ok")
    verificar(_ventana._tab_viz._btn_reimportar.isEnabled(),
              "'Abrir CSV…' no se reactivó tras secuencia_ok")
    print("     secuencia_ok canceló el timer y liberó la GUI por el camino normal")


def caso_3_timeout_libera_con_advertencia_visible():
    _preparar_stop_emergencia(secuencia_running=True)
    _ventana._on_stop_emergencia()
    verificar(_ventana._timer_fallback_detencion.isActive(), "el timer no arrancó para este caso")

    _cajas.limpiar()
    # Se dispara el slot directamente: no tiene sentido esperar 150 s reales
    # en una prueba automatizada, y _on_stop_emergencia ya conectó el timer
    # a este mismo slot — lo que se prueba es el comportamiento del slot,
    # no el reloj de Qt.
    _ventana._on_timeout_detencion_secuencia()

    verificar(_ventana._secuencia_running is False,
              "el timeout no liberó _secuencia_running")
    verificar(_ventana._btn_iniciar_seq.isEnabled() == _ventana._sesion_activa,
              "'Iniciar secuencia' no volvió al estado correcto tras el timeout")
    verificar(_ventana._tab_viz._btn_reimportar.isEnabled(),
              "'Abrir CSV…' no se reactivó tras el timeout")
    verificar(len(_cajas.textos("warning")) == 1,
              f"se esperaba 1 QMessageBox.warning visible, hubo {len(_cajas.textos('warning'))}")
    segundos = str(TIMEOUT_DETENCION_SECUENCIA_MS // 1000)
    verificar(any(segundos in t for t in _cajas.textos("warning")),
              f"la advertencia no menciona el plazo transcurrido ({segundos} s)")
    print(f"     tras el timeout: GUI liberada + {len(_cajas.textos('warning'))} advertencia(s) visible(s)")


def caso_4_secuencia_abortada_cancela_y_timeout_tardio_no_hace_nada():
    _preparar_stop_emergencia(secuencia_running=True)
    _ventana._on_stop_emergencia()
    verificar(_ventana._timer_fallback_detencion.isActive(), "el timer no arrancó para este caso")

    _ventana._on_secuencia_abortada("motivo de prueba")
    verificar(not _ventana._timer_fallback_detencion.isActive(),
              "secuencia_abortada no canceló el timer de respaldo")

    _cajas.limpiar()
    _ventana._on_timeout_detencion_secuencia()  # llega "tarde", tras una resolución normal
    verificar(len(_cajas.textos("warning")) == 0,
              "un timeout tardío (ya resuelto normalmente) mostró una advertencia de más")
    print("     secuencia_abortada canceló el timer; el disparo tardío no hizo nada")


def caso_5_sin_secuencia_no_arranca_timer():
    medicion, _safe = _preparar_stop_emergencia(secuencia_running=False)

    _ventana._on_stop_emergencia()

    verificar(medicion.llamadas_detener == 0,
              "Medicion.detener() se invocó sin haber secuencia activa")
    verificar(not _ventana._timer_fallback_detencion.isActive(),
              "el timer de respaldo arrancó sin secuencia activa")
    verificar(_ventana._btn_iniciar_seq.isEnabled() == _ventana._sesion_activa,
              "la GUI no se liberó de inmediato sin secuencia activa")
    print("     sin secuencia activa: ni Medicion.detener() ni el timer de respaldo se activan")


CASOS = (
    ("1", "Secuencia activa: el stop arranca el timer de respaldo y no libera la GUI",
     caso_1_arranca_timer_y_no_libera),
    ("2", "secuencia_ok llega antes del plazo: cancela el timer, libera por el camino normal",
     caso_2_confirmacion_normal_cancela_timer),
    ("3", "Nadie confirma: al vencer el plazo libera la GUI con advertencia visible",
     caso_3_timeout_libera_con_advertencia_visible),
    ("4", "secuencia_abortada también cancela; un timeout tardío no hace nada de más",
     caso_4_secuencia_abortada_cancela_y_timeout_tardio_no_hace_nada),
    ("5", "Sin secuencia activa, el stop no arranca el timer y libera de inmediato",
     caso_5_sin_secuencia_no_arranca_timer),
)


def main():
    global _ventana, _cajas

    print("=" * 72)
    print("Prueba de la red de seguridad del stop de emergencia (sin hardware)")
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
