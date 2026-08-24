"""
ventana_osciloscopio.py
Ventana de control exclusivo del osciloscopio Tektronix TDS5052B.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pyqtgraph as pg

from PySide6.QtCore import Qt, Signal, Slot, QThread, QObject, QTimer
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QSpinBox,
    QFrame, QFileDialog, QMessageBox, QSplitter,
)

from app.osciloscopio.control_osciloscopio import OsciloscopioController
from app.almacenamiento.almacenamiento import Almacenamiento, PaqueteMedicion
from app.gui.theme import (
    APP_STYLESHEET, LED_VERDE, LED_AMARILLO, LED_ROJO, LED_GRIS,
    make_led, set_led, set_btn_activo,
)


class _CapturaWorker(QObject):
    terminado = Signal(object, object)

    def __init__(self, oscil: OsciloscopioController):
        super().__init__()
        self._oscil = oscil

    @Slot()
    def ejecutar(self):
        captura = self._oscil.leer_pantalla()
        escala = self._oscil.leer_escala_actual() if captura else None
        self.terminado.emit(captura, escala)


class _EscalaWorker(QObject):
    terminado = Signal(object)

    def __init__(self, oscil: OsciloscopioController):
        super().__init__()
        self._oscil = oscil

    @Slot()
    def ejecutar(self):
        self.terminado.emit(self._oscil.leer_escala_actual())


class VentanaOsciloscopio(QMainWindow):

    volver = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Osciloscopio — Tektronix TDS5052B")
        self.setMinimumSize(900, 600)
        self.setStyleSheet(APP_STYLESHEET)

        self._oscil  = OsciloscopioController(self)
        self._store  = Almacenamiento(self)

        self._ultima_captura = None
        self._captura_thread: QThread | None = None
        self._captura_worker: _CapturaWorker | None = None
        self._escala_thread: QThread | None = None
        self._escala_worker: _EscalaWorker | None = None
        self._cerrado = False

        self._adquisicion   = "Sample"
        self._canal_sel: str | None = None
        self._segundos_desde_lectura = 0

        self._timer_lectura = QTimer(self)
        self._timer_lectura.setInterval(1000)
        self._timer_lectura.timeout.connect(self._tick_lectura)

        self._construir_ui()
        self._conectar_signals()

    # ══════════════════════════════════════════════════════════════════════════
    # UI
    # ══════════════════════════════════════════════════════════════════════════

    def _construir_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._topbar())

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._area_senal())
        splitter.addWidget(self._panel_controles())
        splitter.setSizes([560, 300])
        splitter.setStyleSheet("QSplitter::handle { background: #2a2a2a; width: 1px; }")

        wrapper = QWidget()
        wlay = QVBoxLayout(wrapper)
        wlay.setContentsMargins(12, 12, 12, 12)
        wlay.addWidget(splitter)
        root.addWidget(wrapper, 1)

        root.addWidget(self._bottombar())

    def _topbar(self) -> QWidget:
        bar = QFrame()
        bar.setFrameShape(QFrame.StyledPanel)
        bar.setStyleSheet("QFrame { background: #1e1e1e; border-bottom: 1px solid #2e2e2e; }")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(12)

        self._btn_volver = QPushButton("← Volver")
        self._btn_volver.setFixedHeight(30)
        lay.addWidget(self._btn_volver)

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color: #333;")
        lay.addWidget(sep)

        self._led_conn   = make_led(LED_GRIS)
        self._lbl_nombre = QLabel("Sin conexión")
        self._lbl_nombre.setStyleSheet("font-weight: bold; font-size: 14px;")
        lay.addWidget(self._led_conn)
        lay.addWidget(self._lbl_nombre)

        self._chip_estado = QLabel("Desconectado")
        self._chip_estado.setStyleSheet(
            "background: #2a2a2a; color: #666; font-size: 11px;"
            "border: 1px solid #333; border-radius: 4px; padding: 3px 10px;"
        )
        lay.addWidget(self._chip_estado)
        lay.addStretch()

        self._btn_conectar = QPushButton("Conectar")
        self._btn_conectar.setFixedHeight(30)
        self._btn_conectar.setMinimumWidth(110)
        lay.addWidget(self._btn_conectar)
        return bar

    def _area_senal(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: #0d1117;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)

        self._plot = pg.PlotWidget(background="#0d1117")
        self._plot.showGrid(x=True, y=True, alpha=0.15)
        self._plot.setLabel("bottom", "Tiempo", units="s",
                            **{"color": "#3a3a3a", "font-size": "10px"})
        self._plot.setLabel("left", "Voltaje", units="V",
                            **{"color": "#3a3a3a", "font-size": "10px"})
        self._curva = self._plot.plot(pen=pg.mkPen("#00bfff", width=1.5))

        self._lbl_canal_plot = pg.TextItem("", anchor=(0, 0), color="#00bfff")
        self._lbl_vdiv_plot  = pg.TextItem("", anchor=(1, 0), color="#4caf50")
        self._lbl_tdiv_plot  = pg.TextItem("", anchor=(0.5, 1), color="#555")
        self._plot.addItem(self._lbl_canal_plot)
        self._plot.addItem(self._lbl_vdiv_plot)
        self._plot.addItem(self._lbl_tdiv_plot)

        self._overlay = QLabel("Selecciona un canal para comenzar")
        self._overlay.setAlignment(Qt.AlignCenter)
        self._overlay.setStyleSheet("color: #444; font-size: 14px; background: transparent;")

        lay.addWidget(self._plot, 1)
        lay.addWidget(self._overlay)
        self._plot.setVisible(False)
        self._overlay.setVisible(True)
        return w

    def _sep_label(self, texto: str) -> QLabel:
        lbl = QLabel(texto)
        lbl.setStyleSheet("color: #888; font-size: 11px; text-transform: uppercase; margin-top: 2px;")
        return lbl

    def _hline(self) -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.HLine)
        f.setStyleSheet("color: #2a2a2a;")
        return f

    def _valor_lectura(self, titulo: str) -> tuple[QWidget, QLabel]:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        lbl_t = QLabel(titulo)
        lbl_t.setStyleSheet("color: #666; font-size: 10px; text-transform: uppercase;")
        lbl_v = QLabel("—")
        lbl_v.setStyleSheet("color: #ccc; font-size: 13px; font-weight: bold;")
        lay.addWidget(lbl_t)
        lay.addWidget(lbl_v)
        return w, lbl_v

    def _panel_controles(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(270)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        lay.addWidget(self._sep_label("Configuración"))

        # Canal
        lay.addWidget(self._sep_label("Canal de señal"))
        fila_c = QHBoxLayout()
        fila_c.setSpacing(6)
        self._btn_ch1 = QPushButton("CH1")
        self._btn_ch2 = QPushButton("CH2")
        for btn in (self._btn_ch1, self._btn_ch2):
            btn.setFixedHeight(32)
            fila_c.addWidget(btn)
        self._lbl_canal_hint = QLabel("Sin canal seleccionado")
        self._lbl_canal_hint.setStyleSheet("color: #555; font-size: 10px;")
        lay.addLayout(fila_c)
        lay.addWidget(self._lbl_canal_hint)

        # Adquisición
        lay.addWidget(self._sep_label("Modo de adquisición"))
        fila_aq = QHBoxLayout()
        fila_aq.setSpacing(6)
        self._btn_sample  = QPushButton("Sample")
        self._btn_average = QPushButton("Average")
        for btn in (self._btn_sample, self._btn_average):
            btn.setFixedHeight(32)
            fila_aq.addWidget(btn)
        lay.addLayout(fila_aq)

        lay.addWidget(self._sep_label("Nº de promedios"))
        self._spin_numavg = QSpinBox()
        self._spin_numavg.setRange(2, 10000)
        self._spin_numavg.setValue(100)
        self._spin_numavg.setEnabled(False)
        lay.addWidget(self._spin_numavg)

        lay.addWidget(self._hline())

        lay.addWidget(self._sep_label("Del equipo"))

        self._lbl_eq_vertical, self._val_eq_vertical = self._valor_lectura("Vertical")
        self._lbl_eq_horizontal, self._val_eq_horizontal = self._valor_lectura("Horizontal")
        self._lbl_eq_acoplamiento, self._val_eq_acoplamiento = self._valor_lectura("Acoplamiento")
        self._lbl_eq_trigger, self._val_eq_trigger = self._valor_lectura("Trigger")
        self._lbl_eq_puntos, self._val_eq_puntos = self._valor_lectura("Puntos")
        grid_eq = QGridLayout()
        grid_eq.setHorizontalSpacing(10)
        grid_eq.setVerticalSpacing(4)
        celdas = (
            self._lbl_eq_vertical, self._lbl_eq_horizontal,
            self._lbl_eq_acoplamiento, self._lbl_eq_trigger, self._lbl_eq_puntos,
        )
        for i, wid in enumerate(celdas):
            grid_eq.addWidget(wid, i // 2, i % 2)
        lay.addLayout(grid_eq)

        self._lbl_leido_hace = QLabel("Leído hace — s")
        self._lbl_leido_hace.setStyleSheet("color: #555; font-size: 10px; margin-top: 2px;")
        lay.addWidget(self._lbl_leido_hace)

        lay.addStretch()
        return w

    def _bottombar(self) -> QWidget:
        bar = QFrame()
        bar.setFrameShape(QFrame.StyledPanel)
        bar.setStyleSheet("QFrame { background: #1a1a1a; border-top: 1px solid #2e2e2e; }")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(10)

        self._btn_actualizar = QPushButton("Actualizar")
        self._btn_actualizar.setFixedHeight(32)
        self._btn_actualizar.setEnabled(False)

        self._btn_capturar = QPushButton("▶  Capturar señal")
        self._btn_capturar.setFixedHeight(32)
        self._btn_capturar.setEnabled(False)

        self._btn_guardar = QPushButton("⊟  Guardar señal")
        self._btn_guardar.setFixedHeight(32)
        self._btn_guardar.setEnabled(False)

        self._btn_stop_emergencia = QPushButton("⚠  Stop emergencia")
        self._btn_stop_emergencia.setFixedHeight(32)
        self._btn_stop_emergencia.setProperty("peligro", True)
        self._btn_stop_emergencia.style().unpolish(self._btn_stop_emergencia)
        self._btn_stop_emergencia.style().polish(self._btn_stop_emergencia)

        lay.addWidget(self._btn_actualizar)
        lay.addWidget(self._btn_capturar, 1)
        lay.addWidget(self._btn_guardar)
        lay.addStretch()
        lay.addWidget(self._btn_stop_emergencia)
        return bar

    # ══════════════════════════════════════════════════════════════════════════
    # SEÑALES
    # ══════════════════════════════════════════════════════════════════════════

    def _conectar_signals(self):
        self._oscil.led_verde.connect(self._on_oscil_conectado)
        self._oscil.led_rojo.connect(self._on_oscil_desconectado)
        self._oscil.led_amarillo.connect(lambda: set_led(self._led_conn, LED_AMARILLO))
        self._oscil.cmd_ok.connect(lambda t: None)

        self._btn_volver.clicked.connect(self._on_volver)
        self._btn_conectar.clicked.connect(self._on_toggle_conexion)

        self._btn_ch1.clicked.connect(lambda: self._sel_canal("CH1"))
        self._btn_ch2.clicked.connect(lambda: self._sel_canal("CH2"))
        self._btn_sample.clicked.connect(lambda: self._sel_adquisicion("Sample"))
        self._btn_average.clicked.connect(lambda: self._sel_adquisicion("Average"))

        self._btn_actualizar.clicked.connect(self._on_actualizar)
        self._btn_capturar.clicked.connect(self._on_capturar)
        self._btn_guardar.clicked.connect(self._on_guardar)
        self._btn_stop_emergencia.clicked.connect(self._on_stop_emergencia)

        self._sel_adquisicion("Sample")

    # ══════════════════════════════════════════════════════════════════════════
    # SLOTS
    # ══════════════════════════════════════════════════════════════════════════

    @Slot()
    def _on_toggle_conexion(self):
        if self._oscil.conectado:
            self._oscil.desconectar()
            self._btn_conectar.setText("Conectar")
            self._btn_capturar.setEnabled(False)
            self._btn_actualizar.setEnabled(False)
        else:
            if self._oscil.conectar():
                self._btn_conectar.setText("Desconectar")

    @Slot()
    def _on_oscil_conectado(self):
        set_led(self._led_conn, LED_VERDE)
        self._lbl_nombre.setText("TDS5052B")
        self._chip_estado.setText("Conectado — 192.168.1.100")
        self._chip_estado.setStyleSheet(
            "background: #1a3a1a; color: #4caf50; font-size: 11px;"
            "border: 1px solid #2a5a2a; border-radius: 4px; padding: 3px 10px;"
        )
        self._btn_actualizar.setEnabled(True)
        self._btn_capturar.setEnabled(self._canal_sel is not None)
        self._lanzar_sincronizar_escala()

    def _lanzar_sincronizar_escala(self):
        if self._escala_thread is not None and self._escala_thread.isRunning():
            return
        worker = _EscalaWorker(self._oscil)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.ejecutar)
        worker.terminado.connect(self._on_escala_leida)
        worker.terminado.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(worker.deleteLater)
        self._escala_thread = thread
        self._escala_worker = worker
        thread.start()

    @staticmethod
    def _fmt_vdiv(vdiv_v: float) -> str:
        if vdiv_v < 1.0:
            return f"{vdiv_v * 1000:.3g} mV/div"
        return f"{vdiv_v:.3g} V/div"

    @staticmethod
    def _fmt_tdiv(tdiv_s: float) -> str:
        if tdiv_s < 1e-6:
            return f"{tdiv_s * 1e9:.3g} ns/div"
        if tdiv_s < 1e-3:
            return f"{tdiv_s * 1e6:.3g} µs/div"
        if tdiv_s < 1.0:
            return f"{tdiv_s * 1e3:.3g} ms/div"
        return f"{tdiv_s:.3g} s/div"

    @Slot(object)
    def _on_escala_leida(self, escala):
        self._escala_thread = None
        self._escala_worker = None
        if escala is None:
            return

        self._val_eq_vertical.setText(self._fmt_vdiv(escala["vdiv_v"]))
        self._val_eq_horizontal.setText(self._fmt_tdiv(escala["tdiv_s"]))
        self._val_eq_acoplamiento.setText(escala["coupling"])
        self._val_eq_trigger.setText(f"{escala['trigger_v']:.3f} V")
        puntos = escala.get("record_length")
        self._val_eq_puntos.setText(f"{puntos:,}" if puntos is not None else "—")

        if "AVERAGE" in escala["acq_mode"]:
            self._sel_adquisicion("Average")
            self._spin_numavg.setValue(escala["numavg"])
        else:
            self._sel_adquisicion("Sample")

        self._segundos_desde_lectura = 0
        self._lbl_leido_hace.setText("Leído hace 0 s")
        if not self._timer_lectura.isActive():
            self._timer_lectura.start()

    @Slot()
    def _tick_lectura(self):
        self._segundos_desde_lectura += 1
        self._lbl_leido_hace.setText(f"Leído hace {self._segundos_desde_lectura} s")

    @Slot()
    def _on_oscil_desconectado(self):
        set_led(self._led_conn, LED_ROJO)
        self._lbl_nombre.setText("Sin conexión")
        self._chip_estado.setText("Desconectado")
        self._chip_estado.setStyleSheet(
            "background: #2a2a2a; color: #666; font-size: 11px;"
            "border: 1px solid #333; border-radius: 4px; padding: 3px 10px;"
        )
        self._btn_actualizar.setEnabled(False)
        self._btn_capturar.setEnabled(False)
        self._timer_lectura.stop()

    def _sel_canal(self, canal: str):
        self._canal_sel = canal
        set_btn_activo(self._btn_ch1, canal == "CH1", "azul")
        set_btn_activo(self._btn_ch2, canal == "CH2", "azul")
        self._lbl_canal_hint.setText(f"Canal activo: {canal}")
        self._oscil.set_canal(canal)
        if self._oscil.conectado:
            self._btn_capturar.setEnabled(True)
        if not self._plot.isVisible():
            self._overlay.setVisible(False)
            self._plot.setVisible(True)
        self._lbl_canal_plot.setText(canal)

    def _sel_adquisicion(self, modo: str):
        self._adquisicion = modo
        set_btn_activo(self._btn_sample,  modo == "Sample",  "azul")
        set_btn_activo(self._btn_average, modo == "Average", "azul")
        self._spin_numavg.setEnabled(modo == "Average")

    def _refrescar_labels_plot(self, escala=None):
        vr = self._plot.viewRange()
        xr, yr = vr
        self._lbl_canal_plot.setPos(xr[0], yr[1])
        self._lbl_vdiv_plot.setPos(xr[1], yr[1])
        self._lbl_tdiv_plot.setPos((xr[0] + xr[1]) / 2, yr[0])
        if escala is not None:
            self._lbl_vdiv_plot.setText(self._fmt_vdiv(escala["vdiv_v"]))
            self._lbl_tdiv_plot.setText(self._fmt_tdiv(escala["tdiv_s"]))

    @Slot()
    def _on_actualizar(self):
        self._lanzar_sincronizar_escala()

    @Slot()
    def _on_capturar(self):
        if self._captura_thread is not None and self._captura_thread.isRunning():
            return
        self._btn_capturar.setEnabled(False)
        self._btn_actualizar.setEnabled(False)
        self._btn_guardar.setEnabled(False)

        worker = _CapturaWorker(self._oscil)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.ejecutar)
        worker.terminado.connect(self._on_captura_terminada)
        worker.terminado.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(worker.deleteLater)
        self._captura_thread = thread
        self._captura_worker = worker
        thread.start()

    @Slot(object, object)
    def _on_captura_terminada(self, captura, escala):
        self._captura_thread = None
        self._captura_worker = None
        self._btn_capturar.setEnabled(self._oscil.conectado and self._canal_sel is not None)
        self._btn_actualizar.setEnabled(self._oscil.conectado)

        if captura is None:
            QMessageBox.warning(self, "Error de captura",
                                "No se pudo obtener la señal del osciloscopio.")
            return

        self._ultima_captura = captura
        self._btn_guardar.setEnabled(True)
        t = captura.tiempo
        v = captura.voltaje
        mask = np.isfinite(t) & np.isfinite(v)

        if not np.any(mask):
            QMessageBox.warning(self, "Datos inválidos",
                                "La señal capturada no contiene datos válidos.")
            return

        self._curva.setData(t[mask], v[mask])

        if escala is not None:
            self._on_escala_leida(escala)
            vdiv = escala["vdiv_v"]
            tdiv = escala["tdiv_s"]
            t_mid = (t[mask][0] + t[mask][-1]) / 2
            y_mid = captura.wfmpre["YZERO"]
            self._plot.setXRange(t_mid - 5 * tdiv, t_mid + 5 * tdiv, padding=0)
            self._plot.setYRange(y_mid - 4 * vdiv, y_mid + 4 * vdiv, padding=0)
        else:
            t_valid = t[mask]
            v_valid = v[mask]
            self._plot.setXRange(float(t_valid[0]), float(t_valid[-1]), padding=0.05)
            v_min, v_max = float(v_valid.min()), float(v_valid.max())
            margen = max((v_max - v_min) * 0.1, 1e-6)
            self._plot.setYRange(v_min - margen, v_max + margen, padding=0)

        self._refrescar_labels_plot(escala)

    @Slot()
    def _on_guardar(self):
        if self._ultima_captura is None:
            return

        if not self._store.activo:
            carpeta = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta de sesión")
            if not carpeta:
                return
            if not self._store.nueva_sesion(carpeta_base=Path(carpeta)):
                QMessageBox.critical(self, "Error", "No se pudo crear la sesión.")
                return

        paquete = PaqueteMedicion(
            timestamp   = datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            temperatura = 0.0,
            modo        = "manual",
            wfmpre      = self._ultima_captura.wfmpre,
            raw_data    = self._ultima_captura.raw_data,
            error_flag  = self._ultima_captura.error_flag,
        )
        mid = self._store.guardar(paquete)
        if mid:
            self._btn_guardar.setEnabled(False)
            QMessageBox.information(self, "Guardado", f"Medición guardada: {mid}")

    @Slot()
    def _on_stop_emergencia(self):
        QMessageBox.information(
            self,
            "Stop emergencia",
            "Para detener el láser de emergencia usa la ventana \"Láser\" o \"Ambos\",\n"
            "o presiona el botón físico de paro en el equipo.",
        )

    @Slot()
    def _on_volver(self):
        self._cerrar_recursos()
        self.volver.emit()
        self.close()

    def _cerrar_recursos(self):
        if self._cerrado:
            return
        self._cerrado = True
        self._timer_lectura.stop()
        if self._captura_thread is not None and self._captura_thread.isRunning():
            self._captura_thread.quit()
            self._captura_thread.wait(2000)
        if self._escala_thread is not None and self._escala_thread.isRunning():
            self._escala_thread.quit()
            self._escala_thread.wait(2000)
        if self._oscil.conectado:
            self._oscil.desconectar()

    def closeEvent(self, event):
        self._cerrar_recursos()
        event.accept()