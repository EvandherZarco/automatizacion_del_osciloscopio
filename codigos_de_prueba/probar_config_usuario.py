"""
probar_config_usuario.py
Prueba automatizada del ciclo de configuración de usuario (app/config_usuario.py
+ diálogo "Conexión"). Corre sola, sin hardware, con Qt en modo offscreen y un
APPDATA temporal para no tocar el JSON real del usuario.

Casos:
    1. Sin JSON: la aplicación arranca y rigen los valores de app/config.py
       (o config_local.py si existe).
    2. Guardar desde el diálogo escribe el JSON y deja los valores aplicados.
    3. Recargar la configuración devuelve lo guardado.
    4. Cancelar no escribe nada.

Uso:
    venv\\Scripts\\python codigos_de_prueba\\probar_config_usuario.py

Imprime un resumen por caso y termina con código distinto de cero si el
conjunto de casos aprobados no coincide con el esperado.
"""

import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

_APPDATA_TEMPORAL = tempfile.mkdtemp(prefix="config_usuario_prueba_")
os.environ["APPDATA"] = _APPDATA_TEMPORAL
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication

from app import config, config_usuario
from app.gui.bienvenida import BienvenidaWindow
from app.gui.dialogo_conexion import DialogoConexion

CASOS_ESPERADOS = {"1", "2", "3", "4"}
CAMPOS = ("TEMP_COM_PORT", "LASER_COM_PORT", "OSCIL_HOST")

VALORES_NUEVOS = {
    "TEMP_COM_PORT":  "COM7",
    "LASER_COM_PORT": "COM12",
    "OSCIL_HOST":     "192.168.1.77",
}


def verificar(condicion, mensaje):
    if not condicion:
        raise AssertionError(mensaje)


def poner_valores_en_dialogo(dialogo, valores):
    for combo, campo in ((dialogo._cmb_esp32, "TEMP_COM_PORT"),
                         (dialogo._cmb_laser, "LASER_COM_PORT")):
        idx = combo.findData(valores[campo])
        if idx < 0:
            combo.addItem(valores[campo], valores[campo])
            idx = combo.count() - 1
        combo.setCurrentIndex(idx)
    verificar(dialogo._ip_oscil.set_texto(valores["OSCIL_HOST"]),
              f"IP inválida en la prueba: {valores['OSCIL_HOST']}")


# ── Casos ─────────────────────────────────────────────────────────────────────

def caso_1_sin_json():
    ruta = config_usuario.ruta_config()
    verificar(str(ruta).startswith(_APPDATA_TEMPORAL),
              f"la ruta del JSON no está en el APPDATA temporal: {ruta}")
    verificar(not ruta.exists(), "el JSON no debería existir al inicio")

    por_omision = config_usuario.valores_por_omision()
    config_usuario.aplicar()
    ventana = BienvenidaWindow()
    ventana.show()
    verificar(ventana.isVisible(), "la ventana de bienvenida no se mostró")
    ventana.close()

    vigentes = config_usuario.cargar()
    verificar(vigentes == por_omision,
              f"vigentes {vigentes} != por omisión {por_omision}")
    for campo in CAMPOS:
        verificar(getattr(config, campo) == por_omision[campo],
                  f"app.config.{campo} = {getattr(config, campo)!r}, "
                  f"esperado {por_omision[campo]!r}")
    verificar(not ruta.exists(), "arrancar no debe crear el JSON")
    print(f"     valores por omisión: {por_omision}")


def caso_2_guardar_desde_dialogo():
    ruta = config_usuario.ruta_config()
    dialogo = DialogoConexion(None)
    poner_valores_en_dialogo(dialogo, VALORES_NUEVOS)
    dialogo._btn_guardar.click()

    verificar(dialogo.result() == DialogoConexion.Accepted, "Guardar no aceptó el diálogo")
    verificar(dialogo.valores() == VALORES_NUEVOS,
              f"valores() = {dialogo.valores()}, esperado {VALORES_NUEVOS}")
    verificar(ruta.is_file(), "Guardar no escribió el JSON")
    verificar(not ruta.with_name(ruta.name + ".tmp").exists(),
              "quedó el archivo temporal de la escritura atómica")

    contenido = json.loads(ruta.read_text(encoding="utf-8"))
    verificar(contenido == VALORES_NUEVOS, f"JSON escrito: {contenido}")
    for campo in CAMPOS:
        verificar(getattr(config, campo) == VALORES_NUEVOS[campo],
                  f"app.config.{campo} no se aplicó: {getattr(config, campo)!r}")
    print(f"     JSON en {ruta}")


def caso_3_recargar():
    recargado = config_usuario.cargar()
    verificar(recargado == VALORES_NUEVOS,
              f"cargar() = {recargado}, esperado {VALORES_NUEVOS}")
    for campo in CAMPOS:
        verificar(config_usuario.obtener(campo) == VALORES_NUEVOS[campo],
                  f"obtener({campo}) = {config_usuario.obtener(campo)!r}")

    dialogo = DialogoConexion(None)
    verificar(dialogo._cmb_esp32.currentData() == VALORES_NUEVOS["TEMP_COM_PORT"],
              "el diálogo no muestra el puerto del ESP32 guardado")
    verificar(dialogo._cmb_laser.currentData() == VALORES_NUEVOS["LASER_COM_PORT"],
              "el diálogo no muestra el puerto del láser guardado")
    verificar(dialogo._ip_oscil.texto() == VALORES_NUEVOS["OSCIL_HOST"],
              "el diálogo no muestra la IP guardada")
    dialogo.close()
    print(f"     recargado: {recargado}")


def caso_4_cancelar_no_escribe():
    ruta = config_usuario.ruta_config()
    antes = ruta.read_bytes()
    mtime_antes = ruta.stat().st_mtime_ns

    dialogo = DialogoConexion(None)
    poner_valores_en_dialogo(dialogo, {
        "TEMP_COM_PORT":  "COM3",
        "LASER_COM_PORT": "COM4",
        "OSCIL_HOST":     "10.0.0.5",
    })
    dialogo._btn_cancelar.click()

    verificar(dialogo.result() == DialogoConexion.Rejected, "Cancelar no rechazó el diálogo")
    verificar(dialogo.valores() is None, "valores() debe ser None tras cancelar")
    verificar(ruta.read_bytes() == antes, "Cancelar modificó el JSON")
    verificar(ruta.stat().st_mtime_ns == mtime_antes, "Cancelar reescribió el JSON")
    verificar(not ruta.with_name(ruta.name + ".tmp").exists(),
              "Cancelar dejó un archivo temporal")
    verificar(config_usuario.cargar() == VALORES_NUEVOS, "los valores vigentes cambiaron")
    for campo in CAMPOS:
        verificar(getattr(config, campo) == VALORES_NUEVOS[campo],
                  f"app.config.{campo} cambió tras cancelar")
    print("     JSON intacto tras cancelar")


CASOS = (
    ("1", "Sin JSON: arranca con los valores de config.py / config_local.py", caso_1_sin_json),
    ("2", "Guardar desde el diálogo escribe el JSON y aplica los valores", caso_2_guardar_desde_dialogo),
    ("3", "Recargar devuelve lo guardado", caso_3_recargar),
    ("4", "Cancelar no escribe nada", caso_4_cancelar_no_escribe),
)


def main():
    print("=" * 64)
    print("Prueba del ciclo de configuración de usuario")
    print("=" * 64)
    print(f"APPDATA temporal : {_APPDATA_TEMPORAL}")
    print(f"config_local.py  : "
          f"{'presente' if (RAIZ / 'app' / 'config_local.py').is_file() else 'ausente'}")
    print("-" * 64)

    app = QApplication.instance() or QApplication(sys.argv)
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
        print("     OK")

    print("-" * 64)
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
        shutil.rmtree(_APPDATA_TEMPORAL, ignore_errors=True)
    sys.exit(codigo)
