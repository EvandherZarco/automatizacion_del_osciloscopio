"""
visualizacion.py
Tab de visualización de mediciones.
Muestra lista de mediciones, grafica señales y permite exportar la sesión.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QLabel,
    QPushButton,
    QFileDialog,
    QMessageBox,
    QGroupBox,
    QFormLayout,
)

from app.almacenamiento.almacenamiento import Almacenamiento

COLUMNAS_TABLA = ["ID medición", "Timestamp", "Temperatura (°C)", "Modo", "Error"]
COLOR_ERROR = QColor(60, 45, 0)  # fondo amarillo oscuro para dark theme
COLOR_ERROR_FG = QColor(255, 200, 0)


class VisualizacionWidget(QWidget):

    sesion_reimportada = Signal(str)  # ruta CSV reimportada

    def __init__(self, store: Almacenamiento, parent=None):
        super().__init__(parent)
        self._store = store
        self._filas: list[dict] = []
        self._sesion_dir: Path | None = None

        self._construir_ui()
        self._conectar_signals()

    # ──────────────────────────────────────────────────────────────────────────
    # UI
    # ──────────────────────────────────────────────────────────────────────────

    def _construir_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Barra superior
        barra = QHBoxLayout()
        self._lbl_sesion = QLabel("Sin sesión activa")
        self._lbl_sesion.setStyleSheet("color: #aaa; font-size: 11px;")
        self._btn_reimportar = QPushButton("Abrir CSV…")
        self._btn_exportar = QPushButton("Exportar sesión…")
        barra.addWidget(self._lbl_sesion, 1)
        barra.addWidget(self._btn_reimportar)
        barra.addWidget(self._btn_exportar)
        layout.addLayout(barra)

        # Splitter principal: tabla | gráfica
        splitter = QSplitter(Qt.Horizontal)

        # Tabla de mediciones
        self._tabla = QTableWidget(0, len(COLUMNAS_TABLA))
        self._tabla.setHorizontalHeaderLabels(COLUMNAS_TABLA)
        self._tabla.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._tabla.setSelectionBehavior(QTableWidget.SelectRows)
        self._tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        self._tabla.setAlternatingRowColors(True)
        splitter.addWidget(self._tabla)

        # Panel derecho: gráfica + metadatos
        panel_der = QWidget()
        panel_der_lay = QVBoxLayout(panel_der)
        panel_der_lay.setContentsMargins(0, 0, 0, 0)

        self._plot = pg.PlotWidget(background="#1e1e1e")
        self._plot.setLabel("bottom", "Tiempo", units="µs")
        self._plot.setLabel("left", "Voltaje", units="mV")
        self._plot.showGrid(x=True, y=True, alpha=0.3)
        self._plot_curve = self._plot.plot(pen=pg.mkPen("#00bfff", width=1.5))
        panel_der_lay.addWidget(self._plot, 3)

        meta_box = QGroupBox("Metadatos")
        meta_lay = QFormLayout(meta_box)
        self._lbl_ts = QLabel("—")
        self._lbl_temp = QLabel("—")
        self._lbl_modo = QLabel("—")
        self._lbl_flag = QLabel("—")
        meta_lay.addRow("Timestamp:", self._lbl_ts)
        meta_lay.addRow("Temperatura:", self._lbl_temp)
        meta_lay.addRow("Modo:", self._lbl_modo)
        meta_lay.addRow("Error flag:", self._lbl_flag)
        panel_der_lay.addWidget(meta_box, 1)

        splitter.addWidget(panel_der)
        splitter.setSizes([320, 680])
        layout.addWidget(splitter, 1)

    def _conectar_signals(self):
        self._tabla.currentCellChanged.connect(
    lambda curr_row, _c, _pr, _pc: self._on_fila_seleccionada(curr_row)
)
        self._btn_reimportar.clicked.connect(self._reimportar)
        self._btn_exportar.clicked.connect(self._exportar)

    # ──────────────────────────────────────────────────────────────────────────
    # CARGA DE DATOS
    # ──────────────────────────────────────────────────────────────────────────

    def cargar_sesion_activa(self):
        """Carga la sesión que Almacenamiento tiene activa."""
        filas = self._store.cargar_csv()
        if filas is None:
            return
        sid = self._store.session_id or ""
        self._lbl_sesion.setText(f"Sesión: {sid}")
        csv_path = Path(self._store.csv_path) if self._store.csv_path else None
        self._sesion_dir = csv_path.parent if csv_path else None
        self._poblar_tabla(filas)

    def _reimportar(self):
        ruta, _ = QFileDialog.getOpenFileName(
            self, "Abrir sesión", str(Path.home()), "CSV (*.csv)"
        )
        if not ruta:
            return

        ok = self._store.abrir_sesion(ruta)
        if not ok:
            QMessageBox.warning(
                self,
                "Formato incompatible",
                "Este CSV no corresponde a una sesión del sistema.",
            )
            return

        self._sesion_dir = Path(ruta).parent
        self._lbl_sesion.setText(f"Sesión: {Path(ruta).stem}")
        filas = self._store.cargar_csv(ruta)
        if filas:
            self._poblar_tabla(filas)
        self.sesion_reimportada.emit(ruta)

    def _poblar_tabla(self, filas: list[dict]):
        self._filas = filas
        self._tabla.setRowCount(0)

        for fila in filas:
            row = self._tabla.rowCount()
            self._tabla.insertRow(row)

            celdas = [
                fila.get("medicion_id", ""),
                fila.get("timestamp", ""),
                fila.get("temperatura", ""),
                fila.get("modo", ""),
                fila.get("error_flag", "0"),
            ]
            for col, texto in enumerate(celdas):
                item = QTableWidgetItem(str(texto))
                item.setTextAlignment(Qt.AlignCenter)
                if str(fila.get("error_flag", "0")) == "1":
                    item.setBackground(COLOR_ERROR)
                    item.setForeground(COLOR_ERROR_FG)
                self._tabla.setItem(row, col, item)

        if not filas:
            self._plot_curve.setData([], [])
            self._limpiar_meta()

    # ──────────────────────────────────────────────────────────────────────────
    # GRAFICAR SEÑAL
    # ──────────────────────────────────────────────────────────────────────────

    def _on_fila_seleccionada(self, row: int):
        if row < 0 or row >= len(self._filas):
            return

        fila = self._filas[row]
        mid = fila.get("medicion_id", "")

        raw = self._store.cargar_npy(mid)
        if raw is None and self._sesion_dir:
            npy_path = self._sesion_dir / fila.get("archivo_npy", "")
            if npy_path.exists():
                raw = np.load(npy_path)

        if raw is None:
            QMessageBox.warning(
                self,
                "Archivo no encontrado",
                f"No se encontró el archivo .npy para {mid}.\n"
                "Los metadatos del CSV siguen disponibles.",
            )
            self._plot_curve.setData([], [])
        else:
            try:
                xincr = float(fila.get("XINCR", 1))
                xzero = float(fila.get("XZERO", 0))
                pt_off = float(fila.get("PT_OFF", 0))
                ymult = float(fila.get("YMULT", 1))
                yoff = float(fila.get("YOFF", 0))
                yzero = float(fila.get("YZERO", 0))

                indices = np.arange(len(raw), dtype=np.float64)
                tiempo = (xzero + (indices - pt_off) * xincr) * 1e6  # µs
                voltaje = (raw.astype(np.float64) - yoff) * ymult + yzero
                voltaje_mv = voltaje * 1e3  # mV

                self._plot_curve.setData(tiempo, voltaje_mv)
            except Exception as e:
                QMessageBox.warning(self, "Error al graficar", str(e))

        self._actualizar_meta(fila)

    def _actualizar_meta(self, fila: dict):
        flag = str(fila.get("error_flag", "0"))
        self._lbl_ts.setText(fila.get("timestamp", "—"))
        self._lbl_temp.setText(f"{fila.get('temperatura', '—')} °C")
        self._lbl_modo.setText(fila.get("modo", "—"))
        self._lbl_flag.setText("⚠️  Sí" if flag == "1" else "✅  No")
        self._lbl_flag.setStyleSheet(
            "color: #ffc800;" if flag == "1" else "color: #4caf50;"
        )

    def _limpiar_meta(self):
        for lbl in (self._lbl_ts, self._lbl_temp, self._lbl_modo, self._lbl_flag):
            lbl.setText("—")

    # ──────────────────────────────────────────────────────────────────────────
    # EXPORTAR
    # ──────────────────────────────────────────────────────────────────────────

    def _exportar(self):
        if not self._sesion_dir or not self._store.csv_path:
            QMessageBox.information(
                self, "Sin sesión", "No hay sesión activa para exportar."
            )
            return

        destino = QFileDialog.getExistingDirectory(
            self, "Seleccionar carpeta de destino"
        )
        if not destino:
            return

        respuesta = QMessageBox.question(
            self,
            "Exportar",
            "¿Incluir también los archivos .npy?\n\n"
            "Sí → CSV + carpeta completa de señales\n"
            "No → solo el archivo CSV",
            QMessageBox.Yes | QMessageBox.No,
        )

        dest_path = Path(destino)
        try:
            if respuesta == QMessageBox.Yes:
                shutil.copytree(
                    self._sesion_dir,
                    dest_path / self._sesion_dir.name,
                    dirs_exist_ok=True,
                )
            else:
                shutil.copy2(self._store.csv_path, dest_path)

            QMessageBox.information(
                self, "Exportación completa", "Sesión exportada correctamente."
            )
        except Exception as e:
            QMessageBox.critical(self, "Error al exportar", str(e))

    # ──────────────────────────────────────────────────────────────────────────
    # ACTUALIZACIÓN EN VIVO (llamado por Medición mientras corre la secuencia)
    # ──────────────────────────────────────────────────────────────────────────

    def agregar_medicion(self, medicion_id: str):
        """Recarga la tabla desde el CSV para reflejar la nueva medición."""
        filas = self._store.cargar_csv()
        if filas:
            self._poblar_tabla(filas)