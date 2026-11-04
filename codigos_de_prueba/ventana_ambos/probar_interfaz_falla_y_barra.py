"""
probar_interfaz_falla_y_barra.py
Prueba sin hardware de dos detalles de VentanaAmbos (app/gui/ventana_ambos.py):

  - Con el láser en FAULT el banner dice que hay una falla (no que hay que
    detener el láser), "Detener" queda deshabilitado y los controles siguen
    bloqueados. Con RUN se conserva el banner y "Detener" habilitado.
  - Con la ventana angosta, la barra de temperatura (S1-S4 + Prom) no recorta
    el botón "Stop emergencia".

Casos:
    1. FAULT: banner de falla, Detener deshabilitado, controles bloqueados.
    2. RUN: banner de detener el láser y Detener habilitado.
    3. Ventana angosta: el botón de emergencia conserva su ancho completo y
       queda dentro de la barra.

Uso (CMD):
    venv\\Scripts\\python codigos_de_prueba\\ventana_ambos\\probar_interfaz_falla_y_barra.py
"""

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _apoyo import CajasCapturadas, app_qt, crear_ventana_ambos, limpiar  # noqa: E402

CASOS_ESPERADOS = {"1", "2", "3"}

_ventana = None


def verificar(condicion, mensaje):
    if not condicion:
        raise AssertionError(mensaje)


def caso_1_fault_se_ve_como_falla():
    _ventana._fijar_estado_laser("FAULT")
    try:
        texto = _ventana._banner_laser.text()
        verificar(not _ventana._banner_laser.isHidden(), "el banner no se muestra con FAULT")
        verificar("falla" in texto.lower(), f"el banner no habla de falla: {texto!r}")
        verificar("detén" not in texto.lower(), f"el banner sugiere detener el láser: {texto!r}")
        verificar(not _ventana._btn_laser_detener.isEnabled(), "Detener sigue habilitado con FAULT")
        verificar(not _ventana._btn_laser_iniciar.isEnabled(), "Iniciar habilitado con FAULT")
        verificar(not _ventana._btn_p_aplicar_laser.isEnabled(), "Aplicar habilitado con FAULT")
        verificar(not _ventana._btn_p_trigger.isEnabled(), "los controles no quedaron bloqueados")
    finally:
        _ventana._fijar_estado_laser("STOP")
    print(f"     banner {texto!r}, Detener y controles deshabilitados")


def caso_2_run_conserva_banner_y_detener():
    _ventana._fijar_estado_laser("RUN")
    try:
        texto = _ventana._banner_laser.text()
        verificar(not _ventana._banner_laser.isHidden(), "el banner no se muestra con RUN")
        verificar("detén" in texto.lower(), f"el banner de RUN cambió: {texto!r}")
        verificar(_ventana._btn_laser_detener.isEnabled(), "Detener deshabilitado con RUN")
    finally:
        _ventana._fijar_estado_laser("STOP")
    verificar("falla" not in _ventana._banner_laser.text().lower() or _ventana._banner_laser.isHidden(),
              "el banner de falla quedó visible tras volver a STOP")
    print(f"     banner {texto!r}, Detener habilitado")


def caso_3_boton_emergencia_no_se_recorta():
    app = app_qt()
    boton = _ventana._btn_stop_emergencia
    _ventana.show()
    _ventana.resize(300, 700)
    for _ in range(20):
        app.processEvents()
    barra = boton.parentWidget()
    ancho_ideal = boton.sizeHint().width()
    esquina = boton.mapTo(barra, boton.rect().topRight())
    verificar(boton.width() >= ancho_ideal,
              f"botón de {boton.width()} px, necesita {ancho_ideal} px (ventana de {_ventana.width()} px)")
    verificar(esquina.x() <= barra.width(),
              f"el botón termina en x={esquina.x()}, la barra mide {barra.width()} px")
    print(f"     ventana de {_ventana.width()} px: botón de {boton.width()} px (ideal {ancho_ideal}), "
          f"termina en x={esquina.x()} de {barra.width()}")


CASOS = (
    ("1", "FAULT se ve como falla, no como emisión", caso_1_fault_se_ve_como_falla),
    ("2", "RUN conserva el banner y Detener", caso_2_run_conserva_banner_y_detener),
    ("3", "Ventana angosta: el botón de emergencia no se recorta", caso_3_boton_emergencia_no_se_recorta),
)


def main():
    global _ventana
    print("=" * 72)
    print("Banner de falla del láser y barra de temperatura (sin hardware)")
    print("=" * 72)

    _ventana = crear_ventana_ambos(CajasCapturadas())
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
