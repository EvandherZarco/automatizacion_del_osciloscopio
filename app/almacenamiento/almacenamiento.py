"""
almacenamiento.py
Gestión de sesiones de medición: CSV de metadatos + archivos .npy de formas de onda.
Llamado por Medición (modo automático) y GUI (modo manual).
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)

SESIONES_DIR = Path(r"C:\Users\i7\Desktop\automatizacion_Zarco\datos\sesiones")

CSV_HEADER = [
    "timestamp",
    "session_id",
    "medicion_id",
    "temperatura",
    "modo",
    "error_flag",
    "error_desc",
    "XINCR",
    "XZERO",
    "PT_OFF",
    "YMULT",
    "YOFF",
    "YZERO",
    "NR_PT",
    "CH_SCALE",
    "HOR_SCALE",
    "pulsos_estimados",
    "archivo_npy",
]

HEADER_VALIDACION = set(CSV_HEADER)


@dataclass
class PaqueteMedicion:
    timestamp: str
    temperatura: float
    modo: str  # "manual" | "tiempo" | "temperatura"
    wfmpre: dict  # XINCR, XZERO, PT_OFF, YMULT, YOFF, YZERO, NR_PT
    raw_data: np.ndarray
    error_flag: int  # 0 = limpio, 1 = medición con advertencia
    error_desc: str = field(default="")  # descripción legible del error
    pulsos_estimados: int | None = field(default=None)  # solo modo temperatura


class Almacenamiento(QObject):

    sesion_lista = Signal(str)  # session_id de la sesión activa
    guardado_ok = Signal(str)  # medicion_id recién guardado
    guardado_err = Signal(str)  # mensaje de error
    formato_err = Signal(str)  # CSV incompatible al intentar abrir

    def __init__(self, parent=None):
        super().__init__(parent)
        self._session_id: str | None = None
        self._sesion_dir: Path | None = None
        self._csv_path: Path | None = None
        self._medicion_idx: int = 0

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def csv_path(self) -> Path | None:
        return self._csv_path

    @property
    def activo(self) -> bool:
        return self._session_id is not None

    # ──────────────────────────────────────────────────────────────────────────
    # INICIALIZACIÓN DE SESIÓN
    # ──────────────────────────────────────────────────────────────────────────

    def nueva_sesion(self, nombre: str = "", carpeta_base: "Path | None" = None) -> bool:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        sid = f"{ts}_{nombre}" if nombre else ts
        base = carpeta_base if carpeta_base is not None else SESIONES_DIR
        sesion_dir = base / sid
        csv_path = sesion_dir / f"{sid}.csv"

        try:
            sesion_dir.mkdir(parents=True, exist_ok=True)
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(CSV_HEADER)
        except OSError as e:
            self.guardado_err.emit(f"No se pudo crear la sesión: {e}")
            logger.error("nueva_sesion: %s", e)
            return False

        self._session_id = sid
        self._sesion_dir = sesion_dir
        self._csv_path = csv_path
        self._medicion_idx = 0
        self.sesion_lista.emit(sid)
        return True

    def abrir_sesion(self, csv_path: str | Path) -> bool:
        csv_path = Path(csv_path)

        if not csv_path.exists():
            self.formato_err.emit(f"Archivo no encontrado: {csv_path}")
            return False

        if not self._validar_csv(csv_path):
            self.formato_err.emit("Este CSV no corresponde a una sesión del sistema.")
            return False

        sesion_dir = csv_path.parent
        sid = csv_path.stem

        with open(csv_path, "r", encoding="utf-8") as f:
            n_filas = sum(1 for _ in f) - 1

        self._session_id = sid
        self._sesion_dir = sesion_dir
        self._csv_path = csv_path
        self._medicion_idx = max(n_filas, 0)
        self.sesion_lista.emit(sid)
        return True

    # ──────────────────────────────────────────────────────────────────────────
    # GUARDAR MEDICIÓN
    # ──────────────────────────────────────────────────────────────────────────

    def guardar(self, paquete: PaqueteMedicion) -> str | None:
        if not self.activo:
            self.guardado_err.emit("Sin sesión activa.")
            return None

        self._medicion_idx += 1
        medicion_id = f"{self._session_id}_m{self._medicion_idx:04d}"
        npy_nombre = f"{medicion_id}.npy"
        npy_path = self._sesion_dir / npy_nombre

        npy_ok = True
        try:
            np.save(npy_path, paquete.raw_data)
        except OSError as e:
            npy_ok = False
            logger.error("guardar .npy [%s]: %s", medicion_id, e)
            self.guardado_err.emit(f"Error al guardar .npy ({medicion_id}): {e}")

        w = paquete.wfmpre
        fila = [
            paquete.timestamp,
            self._session_id,
            medicion_id,
            paquete.temperatura,
            paquete.modo,
            paquete.error_flag,
            paquete.error_desc,
            w.get("XINCR", ""),
            w.get("XZERO", ""),
            w.get("PT_OFF", ""),
            w.get("YMULT", ""),
            w.get("YOFF", ""),
            w.get("YZERO", ""),
            w.get("NR_PT", ""),
            w.get("CH_SCALE", ""),
            w.get("HOR_SCALE", ""),
            paquete.pulsos_estimados if paquete.pulsos_estimados is not None else "",
            npy_nombre if npy_ok else "",
        ]

        csv_ok = True
        try:
            with open(self._csv_path, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(fila)
        except OSError as e:
            csv_ok = False
            logger.error("guardar CSV [%s]: %s", medicion_id, e)
            self.guardado_err.emit(f"Error al escribir CSV ({medicion_id}): {e}")

        if npy_ok and csv_ok:
            self.guardado_ok.emit(medicion_id)
            return medicion_id

        return None

    # ──────────────────────────────────────────────────────────────────────────
    # LECTURA (para Visualización)
    # ──────────────────────────────────────────────────────────────────────────

    def cargar_csv(self, csv_path: str | Path | None = None) -> list[dict] | None:
        ruta = Path(csv_path) if csv_path else self._csv_path
        if ruta is None or not ruta.exists():
            return None
        with open(ruta, "r", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def cargar_npy(self, medicion_id: str) -> np.ndarray | None:
        if self._sesion_dir is None:
            return None
        npy_path = self._sesion_dir / f"{medicion_id}.npy"
        if not npy_path.exists():
            return None
        return np.load(npy_path)

    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _validar_csv(csv_path: Path) -> bool:
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                header = next(csv.reader(f), None)
            return header is not None and set(header) == HEADER_VALIDACION
        except OSError:
            return False