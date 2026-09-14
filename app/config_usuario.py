"""
config_usuario.py
Parámetros de conexión editables desde la interfaz gráfica.

Se guardan en un JSON dentro de la carpeta del usuario (fuera del repositorio)
y tienen prioridad sobre config_local.py y config.py:

    JSON de usuario  >  app/config_local.py  >  app/config.py

Si el JSON no existe, está corrupto o le faltan campos, se usan los valores
de app.config sin lanzar ninguna excepción: la aplicación siempre arranca.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import NamedTuple

import serial.tools.list_ports

from app import config

CAMPOS = ("TEMP_COM_PORT", "LASER_COM_PORT", "OSCIL_HOST")

_NOMBRE_CARPETA = "automatizacion_osciloscopio"
_NOMBRE_ARCHIVO = "config.json"

_MODULOS_CON_COPIA = (
    "app.laser.control_laser",
    "app.medicion.medicion",
)

_POR_OMISION: dict[str, str] = {campo: getattr(config, campo) for campo in CAMPOS}


class PuertoSerie(NamedTuple):
    nombre: str
    descripcion: str


def ruta_config() -> Path:
    base = os.environ.get("APPDATA")
    raiz = Path(base) if base else Path.home()
    return raiz / _NOMBRE_CARPETA / _NOMBRE_ARCHIVO


def valores_por_omision() -> dict[str, str]:
    """Valores de config.py / config_local.py, sin el JSON de usuario."""
    return dict(_POR_OMISION)


def cargar() -> dict[str, str]:
    """
    Devuelve los tres parámetros vigentes.
    Cada campo toma el valor del JSON si es una cadena no vacía; en cualquier
    otro caso conserva el valor por omisión.
    """
    valores = valores_por_omision()
    try:
        with ruta_config().open("r", encoding="utf-8") as f:
            datos = json.load(f)
    except (OSError, ValueError):
        return valores

    if not isinstance(datos, dict):
        return valores

    for campo in CAMPOS:
        valor = datos.get(campo)
        if isinstance(valor, str) and valor.strip():
            valores[campo] = valor.strip()
    return valores


def obtener(campo: str) -> str:
    return cargar()[campo]


def guardar(valores: dict[str, str]) -> None:
    """
    Escribe el JSON de forma atómica: primero a un archivo temporal en la
    misma carpeta y después lo reemplaza, para que un corte a mitad de la
    escritura no deje un archivo incompleto.
    Lanza OSError si la carpeta o el archivo no se pueden escribir.
    """
    datos = {campo: str(valores[campo]).strip() for campo in CAMPOS}
    ruta = ruta_config()
    ruta.parent.mkdir(parents=True, exist_ok=True)

    temporal = ruta.with_name(ruta.name + ".tmp")
    with temporal.open("w", encoding="utf-8") as f:
        json.dump(datos, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporal, ruta)


def aplicar(valores: dict[str, str] | None = None) -> dict[str, str]:
    """
    Propaga los valores vigentes al módulo app.config y a los módulos que
    copiaron esos nombres con `from app.config import ...` y ya están
    cargados, para que la siguiente conexión use el valor nuevo sin
    reiniciar la aplicación.
    """
    if valores is None:
        valores = cargar()
    for campo in CAMPOS:
        setattr(config, campo, valores[campo])
    for nombre in _MODULOS_CON_COPIA:
        modulo = sys.modules.get(nombre)
        if modulo is None:
            continue
        for campo in CAMPOS:
            if hasattr(modulo, campo):
                setattr(modulo, campo, valores[campo])
    return valores


def enumerar_puertos() -> list[PuertoSerie]:
    """
    Puertos serie presentes en el sistema, con su descripción y fabricante.
    Solo consulta al sistema operativo: no abre ni escribe en ningún puerto.
    """
    puertos = []
    for p in serial.tools.list_ports.comports():
        partes = [p.description or ""]
        if p.manufacturer and p.manufacturer not in partes[0]:
            partes.append(p.manufacturer)
        descripcion = " · ".join(x for x in partes if x and x != "n/a")
        puertos.append(PuertoSerie(p.device, descripcion))
    puertos.sort(key=lambda x: (len(x.nombre), x.nombre))
    return puertos
