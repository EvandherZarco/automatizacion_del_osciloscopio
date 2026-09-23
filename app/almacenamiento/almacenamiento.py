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

SESIONES_DIR = Path(__file__).resolve().parent.parent.parent / "datos" / "sesiones"

CSV_HEADER = [
    "timestamp",
    "session_id",
    "medicion_id",
    "temperatura",
    "t_s1",
    "t_s2",
    "t_s3",
    "t_s4",
    "s1_repetido",
    "s2_repetido",
    "s3_repetido",
    "s4_repetido",
    "t_apertura",
    "t_cierre",
    "duracion_ventana_s",
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
    "acq_mode",
    "numavg",
    "adquisiciones_promediadas",
    "output_level",
    "eo_delay_us",
    "burst_mode",
    "archivo_npy",
]

# Subconjunto mínimo que identifica una sesión del sistema. La validación por
# subconjunto permite abrir sesiones anteriores a la incorporación de columnas
# nuevas, en lugar de rechazarlas por no coincidir exactamente con el header.
COLUMNAS_REQUERIDAS = {
    "timestamp",
    "session_id",
    "medicion_id",
    "temperatura",
    "modo",
    "error_flag",
    "XINCR",
    "XZERO",
    "YMULT",
    "NR_PT",
    "archivo_npy",
}


def _celda(valor) -> object:
    return "" if valor is None else valor


@dataclass
class PaqueteMedicion:
    timestamp: str
    temperatura: float
    modo: str  # "manual" | "tiempo" | "temperatura"
    wfmpre: dict  # XINCR, XZERO, PT_OFF, YMULT, YOFF, YZERO, NR_PT, acq_mode, numavg, adquisiciones_promediadas
    raw_data: np.ndarray
    error_flag: int  # 0 = limpio, 1 = medición con advertencia
    error_desc: str = field(default="")  # descripción legible del error
    output_level: str | None = field(default=None)  # nivel de energía del láser
    eo_delay_us: int | None = field(default=None)  # retardo EO en µs
    burst_mode: str | None = field(default=None)  # modo de disparo del láser
    temps_sensores: list | None = field(default=None)  # S1–S4 en °C; None si el sensor no está presente
    sensores_repetidos: list | None = field(default=None)  # S1–S4: valor idéntico al de la trama anterior
    t_apertura: float | None = field(default=None)  # modo temperatura: °C al abrir la ventana (RUN)
    t_cierre: float | None = field(default=None)  # modo temperatura: °C al cerrar la ventana (STOP)
    duracion_ventana_s: float | None = field(default=None)  # modo temperatura: RUN → STOP en s


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
        self._header: list[str] = CSV_HEADER
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

    def nueva_sesion(
        self,
        nombre: str = "",
        carpeta_base: "Path | None" = None,
        metadatos: "dict | None" = None,
    ) -> bool:
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
        self._header = CSV_HEADER
        self._medicion_idx = 0
        self._escribir_metadatos(metadatos or {})
        self.sesion_lista.emit(sid)
        return True

    def _escribir_metadatos(self, metadatos: dict) -> None:
        """
        Registra en texto plano la identidad del instrumental usado en la sesión.
        Se escribe una sola vez al crearla; un fallo aquí no invalida la sesión.
        """
        if self._sesion_dir is None:
            return

        lineas = [
            f"session_id: {self._session_id}",
            f"fecha_creacion: {datetime.now().isoformat(timespec='seconds')}",
        ]
        for clave, valor in metadatos.items():
            lineas.append(f"{clave}: {valor if valor not in (None, '') else 'no disponible'}")

        try:
            ruta = self._sesion_dir / "metadatos_sesion.txt"
            ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
        except OSError as e:
            logger.warning("No se pudo escribir metadatos_sesion.txt: %s", e)

    def registrar_barrido(self, t_inicial: float, t_final: float, paso: float) -> None:
        """
        Anexa a metadatos_sesion.txt los parámetros de un barrido por
        temperatura, con los valores que recibió el trigger.

        Una misma sesión puede correr varias secuencias sin cerrarse, y sus
        parámetros pueden cambiar entre una y otra. Cada barrido se agrega
        como un bloque numerado con su instante de arranque, sin reescribir
        lo anterior: el archivo conserva la historia completa y las filas
        del CSV se atribuyen a su barrido comparando timestamps.
        """
        if self._sesion_dir is None:
            return

        ruta = self._sesion_dir / "metadatos_sesion.txt"
        prefijo = f"barrido_{self._contar_barridos(ruta) + 1}"
        lineas = [
            f"{prefijo}_inicio: {datetime.now().isoformat(timespec='seconds')}",
            f"{prefijo}_temperatura_inicial_c: {t_inicial}",
            f"{prefijo}_temperatura_final_c: {t_final}",
            f"{prefijo}_paso_c: {paso}",
        ]

        try:
            with open(ruta, "a", encoding="utf-8") as f:
                f.write("\n".join(lineas) + "\n")
        except OSError as e:
            logger.warning("No se pudo registrar el barrido en metadatos_sesion.txt: %s", e)

    @staticmethod
    def _contar_barridos(ruta: Path) -> int:
        """Barridos ya registrados en el archivo, para continuar la numeración."""
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                return sum(
                    1 for linea in f
                    if linea.startswith("barrido_") and "_inicio:" in linea
                )
        except OSError:
            return 0

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
            header = next(csv.reader(f))
            n_filas = sum(1 for _ in f)

        self._session_id = sid
        self._sesion_dir = sesion_dir
        self._csv_path = csv_path
        self._header = header
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

        fila = dict(paquete.wfmpre)
        fila.update({
            "timestamp": paquete.timestamp,
            "session_id": self._session_id,
            "medicion_id": medicion_id,
            "temperatura": paquete.temperatura,
            "modo": paquete.modo,
            "error_flag": paquete.error_flag,
            "error_desc": paquete.error_desc,
            "output_level": paquete.output_level if paquete.output_level is not None else "",
            "eo_delay_us": paquete.eo_delay_us if paquete.eo_delay_us is not None else "",
            "burst_mode": paquete.burst_mode if paquete.burst_mode is not None else "",
            "archivo_npy": npy_nombre if npy_ok else "",
            "t_apertura": _celda(paquete.t_apertura),
            "t_cierre": _celda(paquete.t_cierre),
            "duracion_ventana_s": _celda(paquete.duracion_ventana_s),
        })
        lecturas = paquete.temps_sensores or [None] * 4
        repetidos = paquete.sensores_repetidos or [None] * 4
        for i, (t, repetido) in enumerate(zip(lecturas, repetidos), start=1):
            fila[f"t_s{i}"] = _celda(t)
            fila[f"s{i}_repetido"] = "" if repetido is None else int(repetido)

        csv_ok = True
        try:
            with open(self._csv_path, "a", newline="", encoding="utf-8") as f:
                csv.DictWriter(
                    f, fieldnames=self._header, restval="", extrasaction="ignore"
                ).writerow(fila)
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
            return header is not None and COLUMNAS_REQUERIDAS.issubset(set(header))
        except OSError:
            return False