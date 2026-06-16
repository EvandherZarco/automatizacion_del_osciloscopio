"""
ventana_ambos.py
Ventana principal del sistema fotoacústico.
Control completo de láser + osciloscopio, medición manual y automática.

Flujo obligatorio:
  1. Configurar parámetros del láser  (pestaña Parámetros, panel izquierdo)
  2. Iniciar láser + ajustar oscil    (pestaña Parámetros)
  3. Verificar señal manual           (pestaña Medición, panel Manual)
  4. Guardar señal de prueba          → crea sesión, habilita secuencia automática
  5. Iniciar secuencia automática     (pestaña Medición, panel Automático)
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pyqtgraph as pg

from PySide6.QtCore import Qt, QThread, QTimer, Signal, Slot, QObject
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QPushButton, QComboBox, QSpinBox, QDoubleSpinBox,
    QTabWidget, QFrame, QFileDialog, QMessageBox,
)

from app.config import TEMP_COM_PORT
from app.laser.control_laser import LaserController
from app.osciloscopio.control_osciloscopio import OsciloscopioController
from app.temperatura.temperatura import TempWorker
from app.almacenamiento.almacenamiento import Almacenamiento, PaqueteMedicion
from app.modo_seguro.modo_seguro import ModoSeguro
from app.conexion.monitoreo import MonitoreoConexion, EstadoMonitoreo
from app.medicion.medicion import Medicion
from app.gui.visualizacion import VisualizacionWidget
from app.gui.theme import (
    APP_STYLESHEET, LED_VERDE, LED_AMARILLO, LED_ROJO, LED_GRIS,
    make_led, set_led, set_btn_activo, chip_log,
)

INACTIVIDAD_AVISO_MS   = 60_000
INTERVALO_MIN_TIEMPO_S = 15.0
MONITOREO_LASER_MS     = 10_000


class _ConexionWorker(QObject):
    terminado = Signal()

    def __init__(self, laser, oscil):
        super().__init__()
        self._laser = laser
        self._oscil = oscil

    @Slot()
    def ejecutar(self):
        self._laser.conectar()
        self._oscil.conectar()
        self.terminado.emit()


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


class VentanaAmbos(QMainWindow):

    volver = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sistema de Medición Fotoacústica")
        self.setMinimumSize(1020, 700)
        self.setStyleSheet(APP_STYLESHEET)

        # Módulos
        self._laser = LaserController(self)
        self._oscil = OsciloscopioController(self)
        self._temp  = TempWorker(TEMP_COM_PORT)

        self._temp_thread = QThread(self)
        self._temp.moveToThread(self._temp_thread)
        self._temp_thread.started.connect(self._temp.iniciar)

        self._store    = Almacenamiento(self)
        self._safe     = ModoSeguro(self._laser, self)
        self._monitor  = MonitoreoConexion(
            self._laser, self._oscil, self._temp, self._safe, self)
        self._medicion = Medicion(
            self._laser, self._oscil, self._temp,
            self._store, self._safe, self._monitor, self)

        # Estado
        self._laser_running     = False
        self._secuencia_running = False
        self._sesion_activa     = False
        self._ultima_captura    = None
        self._canal_sel: str | None = None
        self._output_sel        = "E Adjust"
        self._burst_sel         = "Continuous"
        self._acoplamiento      = "DC"
        self._adquisicion       = "Sample"
        self._cerrado           = False

        self._captura_thread: QThread | None = None
        self._captura_worker: _CapturaWorker | None = None

        self._timer_inactividad = QTimer(self)
        self._timer_inactividad.setSingleShot(True)
        self._timer_inactividad.timeout.connect(self._aviso_inactividad)

        self._timer_monitor_laser = QTimer(self)
        self._timer_monitor_laser.setInterval(MONITOREO_LASER_MS)
        self._timer_monitor_laser.timeout.connect(self._actualizar_monitoreo_laser)

        self._construir_ui()
        self._conectar_signals()
        self._iniciar_app()

    # ══════════════════════════════════════════════════════════════════════════
    # UI
    # ══════════════════════════════════════════════════════════════════════════

    def _construir_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._conn_bar())

        self._tab_viz = VisualizacionWidget(self._store, self)
        self._tabs = QTabWidget()
        self._tabs.addTab(self._tab_parametros(), "≡  Parámetros")
        self._tabs.addTab(self._tab_medicion(),   "✦  Medición")
        self._tabs.addTab(self._tab_viz,          "≈  Visualizar datos")

        wrapper = QWidget()
        wlay = QVBoxLayout(wrapper)
        wlay.setContentsMargins(10, 8, 10, 8)
        wlay.addWidget(self._tabs)
        root.addWidget(wrapper, 1)

        root.addWidget(self._bottombar())

    # ── Conn-bar ──────────────────────────────────────────────────────────────

    def _conn_bar(self) -> QWidget:
        bar = QFrame()
        bar.setFrameShape(QFrame.StyledPanel)
        bar.setStyleSheet("QFrame { background: #1e1e1e; border-bottom: 1px solid #2e2e2e; }")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(14)

        self._btn_volver = QPushButton("← Volver")
        self._btn_volver.setFixedHeight(28)
        lay.addWidget(self._btn_volver)

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color: #2e2e2e;")
        lay.addWidget(sep)

        for attr, nombre in (
            ("_led_laser", "Láser"),
            ("_led_oscil", "Osciloscopio"),
            ("_led_esp32", "ESP32"),
        ):
            led = make_led(LED_GRIS)
            setattr(self, attr, led)
            fila = QHBoxLayout()
            fila.setSpacing(5)
            fila.addWidget(led)
            fila.addWidget(QLabel(nombre))
            lay.addLayout(fila)

        self._btn_reconectar_esp32 = QPushButton("↺ Reconectar")
        self._btn_reconectar_esp32.setFixedHeight(22)
        self._btn_reconectar_esp32.setVisible(False)
        self._btn_reconectar_esp32.setStyleSheet("font-size: 11px; padding: 0 6px;")
        lay.addWidget(self._btn_reconectar_esp32)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.VLine)
        sep2.setStyleSheet("color: #2e2e2e;")
        lay.addWidget(sep2)

        self._leds_ds: list[QLabel] = []
        for i in range(4):
            led = make_led(LED_GRIS)
            self._leds_ds.append(led)
            fila = QHBoxLayout()
            fila.setSpacing(4)
            fila.addWidget(led)
            fila.addWidget(QLabel(f"S{i + 1}"))
            lay.addLayout(fila)

        lay.addStretch()

        self._lbl_temp_live = QLabel("—  °C")
        self._lbl_temp_live.setStyleSheet(
            "font-size: 15px; font-weight: bold; color: #00bfff;")
        lay.addWidget(self._lbl_temp_live)
        return bar

    # ── Pestaña Parámetros ────────────────────────────────────────────────────

    def _tab_parametros(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(12)
        lay.addWidget(self._panel_laser_params(), 1)
        lay.addWidget(self._panel_oscil_params(), 1)
        return w

    def _panel_laser_params(self) -> QGroupBox:
        g = QGroupBox()
        lay = QVBoxLayout(g)
        lay.setSpacing(8)

        hdr = QHBoxLayout()
        lbl = QLabel("⚡  Láser")
        lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        self._chip_laser = QLabel("STOP")
        self._chip_laser.setStyleSheet(
            "background: #2a1a1a; color: #f44336; border-radius: 4px;"
            "padding: 2px 8px; font-size: 11px; font-weight: bold;")
        hdr.addWidget(lbl)
        hdr.addStretch()
        hdr.addWidget(self._chip_laser)
        lay.addLayout(hdr)

        fila = QHBoxLayout()
        self._btn_laser_iniciar = QPushButton("▶  Iniciar")
        self._btn_laser_iniciar.setFixedHeight(36)
        self._btn_laser_iniciar.setEnabled(False)
        self._btn_laser_detener = QPushButton("□  Detener")
        self._btn_laser_detener.setFixedHeight(36)
        self._btn_laser_detener.setEnabled(False)
        fila.addWidget(self._btn_laser_iniciar)
        fila.addWidget(self._btn_laser_detener)
        lay.addLayout(fila)

        self._banner_laser = QLabel("🔒  Detén el láser para modificar parámetros")
        self._banner_laser.setAlignment(Qt.AlignCenter)
        self._banner_laser.setStyleSheet(
            "background: #2a1f00; color: #ffc107; border: 1px solid #3a2f00;"
            "border-radius: 6px; padding: 6px; font-size: 11px;")
        self._banner_laser.setVisible(False)
        lay.addWidget(self._banner_laser)

        # Output level
        lay.addWidget(self._sep_lbl("Output level"))
        fila_ol = QHBoxLayout()
        fila_ol.setSpacing(6)
        self._btn_p_e_off = QPushButton("E OFF")
        self._btn_p_e_adj = QPushButton("E Adjust")
        self._btn_p_e_max = QPushButton("E Max")
        for btn in (self._btn_p_e_off, self._btn_p_e_adj, self._btn_p_e_max):
            btn.setFixedHeight(32)
            fila_ol.addWidget(btn)
        lay.addLayout(fila_ol)

        # Burst mode
        lay.addWidget(self._sep_lbl("Burst mode"))
        fila_bm = QHBoxLayout()
        fila_bm.setSpacing(6)
        self._btn_p_cont    = QPushButton("Continuous")
        self._btn_p_burst   = QPushButton("Burst")
        self._btn_p_trigger = QPushButton("Trigger")
        for btn in (self._btn_p_cont, self._btn_p_burst, self._btn_p_trigger):
            btn.setFixedHeight(32)
            fila_bm.addWidget(btn)
        lay.addLayout(fila_bm)

        # Burst length + Cooling T
        campos = QHBoxLayout()
        campos.setSpacing(12)

        col_bl = QVBoxLayout()
        col_bl.addWidget(self._sep_lbl("Burst length"))
        self._spin_p_burst_len = QSpinBox()
        self._spin_p_burst_len.setRange(1, 30000)
        self._spin_p_burst_len.setValue(1)
        self._spin_p_burst_len.setEnabled(False)
        lbl_bl_h = QLabel("pulsos (1–30 000)")
        lbl_bl_h.setStyleSheet("color: #555; font-size: 10px;")
        col_bl.addWidget(self._spin_p_burst_len)
        col_bl.addWidget(lbl_bl_h)

        col_cool = QVBoxLayout()
        col_cool.addWidget(self._sep_lbl("Set cooling T"))
        self._spin_p_cooling = QDoubleSpinBox()
        self._spin_p_cooling.setRange(10.0, 50.0)
        self._spin_p_cooling.setSingleStep(0.1)
        self._spin_p_cooling.setDecimals(1)
        self._spin_p_cooling.setValue(30.0)
        lbl_cool_h = QLabel("°C (10.0–50.0)")
        lbl_cool_h.setStyleSheet("color: #555; font-size: 10px;")
        col_cool.addWidget(self._spin_p_cooling)
        col_cool.addWidget(lbl_cool_h)

        campos.addLayout(col_bl)
        campos.addLayout(col_cool)
        lay.addLayout(campos)

        # EO delay
        lay.addWidget(self._sep_lbl("Adj. EO delay"))
        self._spin_p_eo = QSpinBox()
        self._spin_p_eo.setRange(800, 8000)
        self._spin_p_eo.setValue(3800)
        lbl_eo_h = QLabel("µs — modo seguro = 3800")
        lbl_eo_h.setStyleSheet("color: #555; font-size: 10px;")
        lay.addWidget(self._spin_p_eo)
        lay.addWidget(lbl_eo_h)

        # Monitoreo
        mon = QHBoxLayout()
        mon.setSpacing(6)
        self._card_t_actual = self._metric_card("T actual °C", "—")
        self._card_t_obj    = self._metric_card("T objetivo °C", "—")
        self._card_pulsos   = self._metric_card("Pulsos", "—")
        mon.addWidget(self._card_t_actual)
        mon.addWidget(self._card_t_obj)
        mon.addWidget(self._card_pulsos)
        lay.addLayout(mon)

        lay.addStretch()

        self._btn_p_aplicar_laser = QPushButton("Aplicar parámetros")
        self._btn_p_aplicar_laser.setFixedHeight(34)
        self._btn_p_aplicar_laser.setEnabled(False)
        lay.addWidget(self._btn_p_aplicar_laser)
        return g

    def _panel_oscil_params(self) -> QGroupBox:
        g = QGroupBox()
        lay = QVBoxLayout(g)
        lay.setSpacing(8)

        hdr = QHBoxLayout()
        lbl = QLabel("〰  Osciloscopio")
        lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        self._chip_oscil = QLabel("Desconectado")
        self._chip_oscil.setStyleSheet(
            "background: #2a2a2a; color: #666; border-radius: 4px; padding: 2px 8px; font-size: 11px;")
        hdr.addWidget(lbl)
        hdr.addStretch()
        hdr.addWidget(self._chip_oscil)
        lay.addLayout(hdr)

        lay.addWidget(self._sep_lbl("Canal de señal"))
        fila_c = QHBoxLayout()
        fila_c.setSpacing(6)
        self._btn_p_ch1 = QPushButton("CH1")
        self._btn_p_ch2 = QPushButton("CH2")
        for btn in (self._btn_p_ch1, self._btn_p_ch2):
            btn.setFixedHeight(32)
            fila_c.addWidget(btn)
        lay.addLayout(fila_c)

        lay.addWidget(self._sep_lbl("Escala vertical"))
        self._combo_p_vdiv = QComboBox()
        self._combo_p_vdiv.addItems(list(OsciloscopioController.VDIV_OPCIONES.keys()))
        self._combo_p_vdiv.setCurrentText("100 mV/div")
        lay.addWidget(self._combo_p_vdiv)

        lay.addWidget(self._sep_lbl("Escala horizontal"))
        self._combo_p_tdiv = QComboBox()
        self._combo_p_tdiv.addItems(list(OsciloscopioController.TDIV_OPCIONES.keys()))
        self._combo_p_tdiv.setCurrentText("1 µs/div")
        lay.addWidget(self._combo_p_tdiv)

        lay.addWidget(self._sep_lbl("Acoplamiento"))
        fila_ac = QHBoxLayout()
        fila_ac.setSpacing(6)
        self._btn_p_dc = QPushButton("DC")
        self._btn_p_ac = QPushButton("AC")
        for btn in (self._btn_p_dc, self._btn_p_ac):
            btn.setFixedHeight(32)
            fila_ac.addWidget(btn)
        lay.addLayout(fila_ac)

        lay.addWidget(self._sep_lbl("Trigger level"))
        self._spin_p_trigger = QDoubleSpinBox()
        self._spin_p_trigger.setRange(-10.0, 10.0)
        self._spin_p_trigger.setSingleStep(0.001)
        self._spin_p_trigger.setDecimals(3)
        self._spin_p_trigger.setValue(0.010)
        lbl_tr_h = QLabel("Voltios")
        lbl_tr_h.setStyleSheet("color: #555; font-size: 10px;")
        lay.addWidget(self._spin_p_trigger)
        lay.addWidget(lbl_tr_h)

        lay.addWidget(self._sep_lbl("Adquisición"))
        fila_aq = QHBoxLayout()
        fila_aq.setSpacing(6)
        self._btn_p_sample  = QPushButton("Sample")
        self._btn_p_average = QPushButton("Average")
        for btn in (self._btn_p_sample, self._btn_p_average):
            btn.setFixedHeight(32)
            fila_aq.addWidget(btn)
        lay.addLayout(fila_aq)

        lay.addWidget(self._sep_lbl("Nº promedios"))
        self._spin_p_numavg = QSpinBox()
        self._spin_p_numavg.setRange(2, 10000)
        self._spin_p_numavg.setValue(100)
        self._spin_p_numavg.setEnabled(False)
        lay.addWidget(self._spin_p_numavg)

        lay.addStretch()

        self._btn_p_aplicar_oscil = QPushButton("Aplicar parámetros")
        self._btn_p_aplicar_oscil.setFixedHeight(34)
        self._btn_p_aplicar_oscil.setEnabled(False)
        lay.addWidget(self._btn_p_aplicar_oscil)
        return g

    # ── Pestaña Medición ──────────────────────────────────────────────────────

    def _tab_medicion(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(12)
        lay.addWidget(self._panel_manual(), 1)
        lay.addWidget(self._panel_auto(), 1)
        return w

    def _panel_manual(self) -> QGroupBox:
        g = QGroupBox()
        lay = QVBoxLayout(g)
        lay.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("🔄"))
        lbl = QLabel("Modo Manual")
        lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        hdr.addWidget(lbl)
        hdr.addStretch()
        lay.addLayout(hdr)

        desc = QLabel("Captura bajo demanda. Verifica la señal antes de la secuencia automática.")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #666; font-size: 11px;")
        lay.addWidget(desc)

        self._plot_manual = pg.PlotWidget(background="#0d1117")
        self._plot_manual.setMaximumHeight(180)
        self._plot_manual.showGrid(x=True, y=True, alpha=0.12)
        self._plot_manual.setLabel("bottom", "Tiempo", units="µs",
                                   **{"color": "#333", "font-size": "9px"})
        self._plot_manual.setLabel("left", "Voltaje", units="mV",
                                   **{"color": "#333", "font-size": "9px"})
        self._curva_manual = self._plot_manual.plot(pen=pg.mkPen("#00bfff", width=1.5))
        self._lbl_canal_m  = pg.TextItem("", anchor=(0, 0), color="#00bfff")
        self._lbl_tdiv_m   = pg.TextItem("", anchor=(0.5, 1), color="#555")
        self._plot_manual.addItem(self._lbl_canal_m)
        self._plot_manual.addItem(self._lbl_tdiv_m)
        lay.addWidget(self._plot_manual)

        fila = QHBoxLayout()
        fila.setSpacing(8)
        self._btn_capturar = QPushButton("⊙  Capturar")
        self._btn_capturar.setFixedHeight(34)
        self._btn_capturar.setEnabled(False)
        self._btn_guardar_manual = QPushButton("⊟  Guardar")
        self._btn_guardar_manual.setFixedHeight(34)
        self._btn_guardar_manual.setEnabled(False)
        fila.addWidget(self._btn_capturar)
        fila.addWidget(self._btn_guardar_manual)
        lay.addLayout(fila)
        lay.addStretch()
        return g

    def _panel_auto(self) -> QGroupBox:
        g = QGroupBox()
        lay = QVBoxLayout(g)
        lay.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("⚙"))
        lbl = QLabel("Modo Automático")
        lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        hdr.addWidget(lbl)
        hdr.addStretch()
        lay.addLayout(hdr)

        lay.addWidget(self._sep_lbl("Modo de secuencia"))
        fila_ms = QHBoxLayout()
        fila_ms.setSpacing(6)
        self._btn_por_tiempo = QPushButton("Por tiempo")
        self._btn_por_temp   = QPushButton("Por temperatura")
        for btn in (self._btn_por_tiempo, self._btn_por_temp):
            btn.setFixedHeight(32)
            fila_ms.addWidget(btn)
        lay.addLayout(fila_ms)

        # Config tiempo
        self._grp_tiempo = QWidget()
        gt = QHBoxLayout(self._grp_tiempo)
        gt.setContentsMargins(0, 0, 0, 0)
        gt.setSpacing(10)
        col_n = QVBoxLayout()
        lbl_n = self._sep_lbl("Nº mediciones")
        self._spin_n_med = QSpinBox()
        self._spin_n_med.setRange(1, 9999)
        self._spin_n_med.setValue(10)
        col_n.addWidget(lbl_n)
        col_n.addWidget(self._spin_n_med)
        col_i = QVBoxLayout()
        lbl_i = self._sep_lbl("Intervalo (s)")
        self._spin_intervalo = QDoubleSpinBox()
        self._spin_intervalo.setRange(INTERVALO_MIN_TIEMPO_S, 3600)
        self._spin_intervalo.setValue(60)
        col_i.addWidget(lbl_i)
        col_i.addWidget(self._spin_intervalo)
        gt.addLayout(col_n)
        gt.addLayout(col_i)
        lay.addWidget(self._grp_tiempo)
        lbl_hint_t = QLabel("Tiempo entre el inicio de una medición y la siguiente.")
        lbl_hint_t.setStyleSheet("color: #555; font-size: 10px;")
        lay.addWidget(lbl_hint_t)

        # Config temperatura
        self._grp_temp_cfg = QWidget()
        gtp = QHBoxLayout(self._grp_temp_cfg)
        gtp.setContentsMargins(0, 0, 0, 0)
        gtp.setSpacing(10)
        for attr, label, val in (
            ("_spin_t_ini",  "T inicial (°C)", 35.0),
            ("_spin_t_fin",  "T final (°C)",   20.0),
            ("_spin_t_paso", "Paso (°C)",       1.0),
        ):
            col = QVBoxLayout()
            col.addWidget(self._sep_lbl(label))
            sp = QDoubleSpinBox()
            sp.setRange(0.0, 100.0)
            sp.setSingleStep(0.5)
            sp.setValue(val)
            setattr(self, attr, sp)
            col.addWidget(sp)
            gtp.addLayout(col)
        self._grp_temp_cfg.setVisible(False)
        lay.addWidget(self._grp_temp_cfg)

        lay.addStretch()

        self._lbl_progreso = QLabel("—")
        self._lbl_progreso.setStyleSheet("color: #666; font-size: 11px;")
        lay.addWidget(self._lbl_progreso)

        self._btn_iniciar_seq = QPushButton("▶  Iniciar secuencia")
        self._btn_iniciar_seq.setFixedHeight(36)
        self._btn_iniciar_seq.setEnabled(False)
        self._btn_iniciar_seq.setToolTip("Realiza y guarda una captura manual primero")

        self._btn_detener_seq = QPushButton("□  Detener")
        self._btn_detener_seq.setFixedHeight(36)
        self._btn_detener_seq.setEnabled(False)

        lay.addWidget(self._btn_iniciar_seq)
        lay.addWidget(self._btn_detener_seq)
        return g

    # ── Bottombar ─────────────────────────────────────────────────────────────

    def _bottombar(self) -> QWidget:
        bar = QFrame()
        bar.setFrameShape(QFrame.StyledPanel)
        bar.setStyleSheet("QFrame { background: #1a1a1a; border-top: 1px solid #2e2e2e; }")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(10)
        self._lbl_log = chip_log("—")
        lay.addWidget(self._lbl_log, 1)
        self._btn_stop_emergencia = QPushButton("⚠  Stop emergencia")
        self._btn_stop_emergencia.setFixedHeight(32)
        self._btn_stop_emergencia.setProperty("peligro", True)
        self._btn_stop_emergencia.style().unpolish(self._btn_stop_emergencia)
        self._btn_stop_emergencia.style().polish(self._btn_stop_emergencia)
        lay.addWidget(self._btn_stop_emergencia)
        return bar

    # ── Helpers UI ────────────────────────────────────────────────────────────

    def _sep_lbl(self, texto: str) -> QLabel:
        lbl = QLabel(texto)
        lbl.setStyleSheet("color: #888; font-size: 11px; text-transform: uppercase;")
        return lbl

    def _metric_card(self, titulo: str, valor: str) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: #252525; border-radius: 6px;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(2)
        lbl_t = QLabel(titulo)
        lbl_t.setStyleSheet("color: #777; font-size: 10px; background: transparent;")
        lbl_v = QLabel(valor)
        lbl_v.setStyleSheet("font-size: 16px; font-weight: bold; background: transparent;")
        lay.addWidget(lbl_t)
        lay.addWidget(lbl_v)
        w._lbl_valor = lbl_v
        return w

    # ══════════════════════════════════════════════════════════════════════════
    # SEÑALES
    # ══════════════════════════════════════════════════════════════════════════

    def _conectar_signals(self):
        # Laser LEDs
        self._laser.led_verde.connect(lambda: set_led(self._led_laser, LED_VERDE))
        self._laser.led_amarillo.connect(lambda: set_led(self._led_laser, LED_AMARILLO))
        self._laser.led_rojo.connect(lambda: set_led(self._led_laser, LED_ROJO))
        self._laser.led_verde.connect(self._on_laser_conectado)
        self._laser.led_rojo.connect(self._on_laser_desconectado)
        self._laser.cmd_ok.connect(self._set_log)
        self._laser.error.connect(self._set_log)

        # Monitor laser LEDs
        self._monitor.laser_led_verde.connect(lambda: set_led(self._led_laser, LED_VERDE))
        self._monitor.laser_led_amarillo.connect(lambda: set_led(self._led_laser, LED_AMARILLO))
        self._monitor.laser_led_rojo.connect(lambda: set_led(self._led_laser, LED_ROJO))

        # Oscil LEDs
        self._oscil.led_verde.connect(lambda: set_led(self._led_oscil, LED_VERDE))
        self._oscil.led_amarillo.connect(lambda: set_led(self._led_oscil, LED_AMARILLO))
        self._oscil.led_rojo.connect(lambda: set_led(self._led_oscil, LED_ROJO))
        self._oscil.led_verde.connect(self._on_oscil_conectado)
        self._oscil.led_rojo.connect(self._on_oscil_desconectado)
        self._oscil.cmd_ok.connect(self._set_log)
        self._oscil.error.connect(self._set_log)

        self._monitor.oscil_led_verde.connect(lambda: set_led(self._led_oscil, LED_VERDE))
        self._monitor.oscil_led_amarillo.connect(lambda: set_led(self._led_oscil, LED_AMARILLO))
        self._monitor.oscil_led_rojo.connect(lambda: set_led(self._led_oscil, LED_ROJO))

        # ESP32 LEDs
        self._monitor.esp32_led_verde.connect(lambda: set_led(self._led_esp32, LED_VERDE))
        self._monitor.esp32_led_amarillo.connect(lambda: set_led(self._led_esp32, LED_AMARILLO))
        self._monitor.esp32_led_rojo.connect(lambda: set_led(self._led_esp32, LED_ROJO))
        self._temp.trigger.connect(lambda t: self._lbl_temp_live.setText(f"{t:.2f}  °C"))
        self._temp.desconectado.connect(self._on_esp32_desconectado)
        self._temp.conectado.connect(self._on_esp32_conectado)
        self._btn_reconectar_esp32.clicked.connect(self._reconectar_esp32)

        # DS18B20 LEDs
        self._monitor.ds_led_verde.connect(lambda i: set_led(self._leds_ds[i], LED_VERDE))
        self._monitor.ds_led_rojo.connect(lambda i: set_led(self._leds_ds[i], LED_ROJO))

        # Monitor seguridad
        self._monitor.seguridad_activada.connect(self._on_seguridad_activada)

        # Medición
        self._medicion.medicion_guardada.connect(self._on_medicion_guardada)
        self._medicion.secuencia_ok.connect(self._on_secuencia_ok)
        self._medicion.secuencia_abortada.connect(self._on_secuencia_abortada)

        # Topbar
        self._btn_volver.clicked.connect(self._on_volver)

        # Parámetros — láser
        self._btn_laser_iniciar.clicked.connect(self._on_laser_iniciar)
        self._btn_laser_detener.clicked.connect(self._on_laser_detener)
        self._btn_p_e_off.clicked.connect(lambda: self._sel_output("E OFF"))
        self._btn_p_e_adj.clicked.connect(lambda: self._sel_output("E Adjust"))
        self._btn_p_e_max.clicked.connect(lambda: self._sel_output("E Max"))
        self._btn_p_cont.clicked.connect(lambda: self._sel_burst("Continuous"))
        self._btn_p_burst.clicked.connect(lambda: self._sel_burst("Burst"))
        self._btn_p_trigger.clicked.connect(lambda: self._sel_burst("Trigger"))
        self._btn_p_aplicar_laser.clicked.connect(self._on_aplicar_laser)

        # Parámetros — oscil
        self._btn_p_ch1.clicked.connect(lambda: self._sel_canal("CH1"))
        self._btn_p_ch2.clicked.connect(lambda: self._sel_canal("CH2"))
        self._btn_p_dc.clicked.connect(lambda: self._sel_acoplamiento("DC"))
        self._btn_p_ac.clicked.connect(lambda: self._sel_acoplamiento("AC"))
        self._btn_p_sample.clicked.connect(lambda: self._sel_adquisicion("Sample"))
        self._btn_p_average.clicked.connect(lambda: self._sel_adquisicion("Average"))
        self._btn_p_aplicar_oscil.clicked.connect(self._on_aplicar_oscil)

        # Medición
        self._btn_capturar.clicked.connect(self._on_capturar)
        self._btn_guardar_manual.clicked.connect(self._on_guardar_manual)
        self._btn_por_tiempo.clicked.connect(lambda: self._sel_modo_auto("tiempo"))
        self._btn_por_temp.clicked.connect(lambda: self._sel_modo_auto("temperatura"))
        self._btn_iniciar_seq.clicked.connect(self._on_iniciar_secuencia)
        self._btn_detener_seq.clicked.connect(self._on_detener_secuencia)

        # Stop emergencia
        self._btn_stop_emergencia.clicked.connect(self._on_stop_emergencia)

        # Estado inicial de selectores
        self._sel_output("E Adjust")
        self._sel_burst("Continuous")
        self._sel_acoplamiento("DC")
        self._sel_adquisicion("Sample")
        self._sel_modo_auto("tiempo")

    # ══════════════════════════════════════════════════════════════════════════
    # INICIALIZACIÓN
    # ══════════════════════════════════════════════════════════════════════════

    def _iniciar_app(self):
        worker = _ConexionWorker(self._laser, self._oscil)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.ejecutar)
        worker.terminado.connect(self._post_conexion)
        worker.terminado.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(worker.deleteLater)
        self._init_thread = thread
        self._init_worker = worker
        thread.start()

    def _post_conexion(self):
        self._temp_thread.start()
        self._monitor.iniciar()

    # ══════════════════════════════════════════════════════════════════════════
    # SLOTS — CONEXIÓN
    # ══════════════════════════════════════════════════════════════════════════

    @Slot()
    def _on_laser_conectado(self):
        self._chip_laser.setText("STOP")
        self._chip_laser.setStyleSheet(
            "background: #2a1a1a; color: #f44336; border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: bold;")
        self._btn_laser_iniciar.setEnabled(not self._laser_running)
        self._btn_p_aplicar_laser.setEnabled(not self._laser_running)
        self._timer_monitor_laser.start()
        self._actualizar_monitoreo_laser()

    @Slot()
    def _on_laser_desconectado(self):
        self._btn_laser_iniciar.setEnabled(False)
        self._btn_laser_detener.setEnabled(False)
        self._btn_p_aplicar_laser.setEnabled(False)
        self._timer_monitor_laser.stop()

    @Slot()
    def _on_oscil_conectado(self):
        self._chip_oscil.setText("Conectado")
        self._chip_oscil.setStyleSheet(
            "background: #1a3a1a; color: #4caf50; border-radius: 4px; padding: 2px 8px; font-size: 11px;")
        self._btn_p_aplicar_oscil.setEnabled(True)
        self._btn_capturar.setEnabled(self._canal_sel is not None)

    @Slot()
    def _on_oscil_desconectado(self):
        self._chip_oscil.setText("Desconectado")
        self._chip_oscil.setStyleSheet(
            "background: #2a2a2a; color: #666; border-radius: 4px; padding: 2px 8px; font-size: 11px;")
        self._btn_p_aplicar_oscil.setEnabled(False)
        self._btn_capturar.setEnabled(False)

    @Slot()
    def _on_esp32_desconectado(self):
        set_led(self._led_esp32, LED_ROJO)
        self._btn_reconectar_esp32.setVisible(True)
        self._btn_reconectar_esp32.setEnabled(True)
        self._temp_thread.quit()

    @Slot(list)
    def _on_esp32_conectado(self, _sensores):
        self._btn_reconectar_esp32.setVisible(False)

    @Slot()
    def _reconectar_esp32(self):
        if self._temp_thread.isRunning():
            return
        self._btn_reconectar_esp32.setEnabled(False)
        self._temp_thread.start()

    # ══════════════════════════════════════════════════════════════════════════
    # SLOTS — PARÁMETROS LÁSER
    # ══════════════════════════════════════════════════════════════════════════

    def _sel_output(self, modo: str):
        self._output_sel = modo
        mapa = {
            "E OFF":    (self._btn_p_e_off, "amber"),
            "E Adjust": (self._btn_p_e_adj, "azul"),
            "E Max":    (self._btn_p_e_max, "verde"),
        }
        for m, (btn, est) in mapa.items():
            set_btn_activo(btn, m == modo, est)

    def _sel_burst(self, modo: str):
        self._burst_sel = modo
        mapa = {
            "Continuous": self._btn_p_cont,
            "Burst":      self._btn_p_burst,
            "Trigger":    self._btn_p_trigger,
        }
        for m, btn in mapa.items():
            set_btn_activo(btn, m == modo, "azul")
        self._spin_p_burst_len.setEnabled(modo != "Continuous" and not self._laser_running)

    @Slot()
    def _on_laser_iniciar(self):
        resp = QMessageBox.warning(
            self, "Encender el láser",
            "El láser está a punto de disparar.\n\n"
            "Verifique que las protecciones estén en su lugar.",
            QMessageBox.Ok | QMessageBox.Cancel,
        )
        if resp != QMessageBox.Ok:
            return
        if self._laser.start():
            self._laser_running = True
            self._actualizar_ui_laser()
            self._reiniciar_timer_inactividad()

    @Slot()
    def _on_laser_detener(self):
        if self._laser.stop():
            self._laser_running = False
            self._timer_inactividad.stop()
            self._actualizar_ui_laser()

    def _actualizar_ui_laser(self):
        c = self._laser_running
        self._chip_laser.setText("RUN" if c else "STOP")
        self._chip_laser.setStyleSheet(
            f"background: {'#1a3a1a' if c else '#2a1a1a'};"
            f"color: {'#4caf50' if c else '#f44336'};"
            "border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: bold;")
        self._banner_laser.setVisible(c)
        self._btn_laser_iniciar.setEnabled(not c and self._laser.conectado)
        self._btn_laser_detener.setEnabled(c)
        self._btn_p_aplicar_laser.setEnabled(not c and self._laser.conectado)
        for w in (self._btn_p_e_off, self._btn_p_e_adj, self._btn_p_e_max,
                  self._btn_p_cont, self._btn_p_burst, self._btn_p_trigger,
                  self._spin_p_cooling, self._spin_p_eo):
            w.setEnabled(not c)
        if not c:
            self._sel_burst(self._burst_sel)

    @Slot()
    def _on_aplicar_laser(self):
        self._laser.set_output_level(self._output_sel)
        self._laser.set_burst_mode(self._burst_sel)
        if self._burst_sel != "Continuous":
            self._laser.set_burst_length(self._spin_p_burst_len.value())
        self._laser.set_cooling_temp(self._spin_p_cooling.value())
        self._laser.set_eo_delay(self._spin_p_eo.value())
        self._set_log("Parámetros del láser aplicados")

    def _actualizar_monitoreo_laser(self):
        t = self._laser.read_cooling_temp()
        if t is not None:
            self._card_t_actual._lbl_valor.setText(f"{t:.1f}")
        p = self._laser.read_pulse_counter()
        if p is not None:
            self._card_pulsos._lbl_valor.setText(f"{p:,}")
        self._card_t_obj._lbl_valor.setText(f"{self._spin_p_cooling.value():.1f}")

    # ══════════════════════════════════════════════════════════════════════════
    # SLOTS — PARÁMETROS OSCILOSCOPIO
    # ══════════════════════════════════════════════════════════════════════════

    def _sel_canal(self, canal: str):
        self._canal_sel = canal
        set_btn_activo(self._btn_p_ch1, canal == "CH1", "azul")
        set_btn_activo(self._btn_p_ch2, canal == "CH2", "azul")
        self._oscil.set_canal(canal)
        if self._oscil.conectado:
            self._btn_capturar.setEnabled(True)

    def _sel_acoplamiento(self, modo: str):
        self._acoplamiento = modo
        set_btn_activo(self._btn_p_dc, modo == "DC", "azul")
        set_btn_activo(self._btn_p_ac, modo == "AC", "azul")

    def _sel_adquisicion(self, modo: str):
        self._adquisicion = modo
        set_btn_activo(self._btn_p_sample,  modo == "Sample",  "azul")
        set_btn_activo(self._btn_p_average, modo == "Average", "azul")
        self._spin_p_numavg.setEnabled(modo == "Average")

    @Slot()
    def _on_aplicar_oscil(self):
        self._oscil.aplicar_parametros(
            vdiv      = self._combo_p_vdiv.currentText(),
            tdiv      = self._combo_p_tdiv.currentText(),
            coupling  = self._acoplamiento,
            trigger_v = self._spin_p_trigger.value(),
            acq_mode  = "AVERAGE" if self._adquisicion == "Average" else "SAMPLE",
            numavg    = self._spin_p_numavg.value(),
        )

    # ══════════════════════════════════════════════════════════════════════════
    # SLOTS — MEDICIÓN MANUAL
    # ══════════════════════════════════════════════════════════════════════════

    @Slot()
    def _on_capturar(self):
        if self._captura_thread is not None and self._captura_thread.isRunning():
            return
        self._btn_capturar.setEnabled(False)
        self._btn_guardar_manual.setEnabled(False)
        self._reiniciar_timer_inactividad()

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

        if captura is None:
            QMessageBox.warning(self, "Error de captura",
                                "No se pudo obtener la señal del osciloscopio.")
            return

        t = captura.tiempo
        v = captura.voltaje
        mask = np.isfinite(t) & np.isfinite(v)

        if not np.any(mask):
            QMessageBox.warning(self, "Datos inválidos",
                                "La señal capturada no contiene datos válidos.")
            return

        self._ultima_captura = captura
        self._btn_guardar_manual.setEnabled(True)

        t_us = t[mask] * 1e6
        v_mv = v[mask] * 1e3
        self._curva_manual.setData(t_us, v_mv)

        if escala is not None:
            vdiv_mv = escala["vdiv_v"] * 1e3
            tdiv_us = escala["tdiv_s"] * 1e6
            t_mid = (t_us[0] + t_us[-1]) / 2
            y_mid = captura.wfmpre["YZERO"] * 1e3
            self._plot_manual.setXRange(t_mid - 5 * tdiv_us, t_mid + 5 * tdiv_us, padding=0)
            self._plot_manual.setYRange(y_mid - 4 * vdiv_mv, y_mid + 4 * vdiv_mv, padding=0)
        else:
            self._plot_manual.setXRange(float(t_us[0]), float(t_us[-1]), padding=0.05)
            v_min, v_max = float(v_mv.min()), float(v_mv.max())
            margen = max((v_max - v_min) * 0.1, 1e-6)
            self._plot_manual.setYRange(v_min - margen, v_max + margen, padding=0)

        vr = self._plot_manual.viewRange()
        xr, yr = vr
        self._lbl_canal_m.setPos(xr[0], yr[1])
        self._lbl_canal_m.setText(self._canal_sel or "")
        self._lbl_tdiv_m.setPos((xr[0] + xr[1]) / 2, yr[0])
        self._lbl_tdiv_m.setText(self._combo_p_tdiv.currentText())

    @Slot()
    def _on_guardar_manual(self):
        if self._ultima_captura is None:
            return

        if not self._sesion_activa:
            carpeta = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta de sesión")
            if not carpeta:
                return
            if not self._store.nueva_sesion(carpeta_base=Path(carpeta)):
                QMessageBox.critical(self, "Error", "No se pudo crear la sesión.")
                return

        temp, _, _ = self._temp.consultar()
        paquete = PaqueteMedicion(
            timestamp   = datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            temperatura = temp if temp is not None else 0.0,
            modo        = "manual",
            wfmpre      = self._ultima_captura.wfmpre,
            raw_data    = self._ultima_captura.raw_data,
            error_flag  = 1 if self._monitor.error_flag else self._ultima_captura.error_flag,
        )
        mid = self._store.guardar(paquete)
        if mid:
            self._btn_guardar_manual.setEnabled(False)
            self._sesion_activa = True
            self._btn_iniciar_seq.setEnabled(True)
            self._btn_iniciar_seq.setToolTip("")
            self._set_log(f"Guardado: {mid}")
            self._tab_viz.cargar_sesion_activa()

    # ══════════════════════════════════════════════════════════════════════════
    # SLOTS — MODO AUTOMÁTICO
    # ══════════════════════════════════════════════════════════════════════════

    def _sel_modo_auto(self, modo: str):
        set_btn_activo(self._btn_por_tiempo, modo == "tiempo",      "azul")
        set_btn_activo(self._btn_por_temp,   modo == "temperatura", "azul")
        self._grp_tiempo.setVisible(modo == "tiempo")
        self._grp_temp_cfg.setVisible(modo == "temperatura")

    def _modo_auto_activo(self) -> str:
        return "temperatura" if self._btn_por_temp.property("activo") else "tiempo"

    @Slot()
    def _on_iniciar_secuencia(self):
        if not self._laser.conectado:
            QMessageBox.warning(self, "Láser desconectado",
                                "El láser debe estar conectado para iniciar la secuencia.")
            return

        if not self._laser_running:
            QMessageBox.warning(self, "Láser apagado",
                                "Enciende el láser antes de iniciar la secuencia automática.")
            return

        if not self._oscil.conectado:
            QMessageBox.warning(self, "Osciloscopio desconectado",
                                "El osciloscopio debe estar conectado para iniciar la secuencia.")
            return

        por_temp = self._modo_auto_activo() == "temperatura"
        if por_temp and not self._temp.esta_conectado():
            QMessageBox.warning(self, "ESP32 desconectado",
                                "El módulo de temperatura debe estar conectado para este modo.")
            return

        resp = QMessageBox.warning(
            self, "Iniciar secuencia automática",
            "La secuencia tomará mediciones de forma autónoma.\n\n¿Continuar?",
            QMessageBox.Ok | QMessageBox.Cancel,
        )
        if resp != QMessageBox.Ok:
            return

        self._secuencia_running = True
        self._timer_inactividad.stop()
        self._btn_iniciar_seq.setEnabled(False)
        self._btn_detener_seq.setEnabled(True)
        self._btn_por_tiempo.setEnabled(False)
        self._btn_por_temp.setEnabled(False)
        self._lbl_progreso.setText("Secuencia en curso…")
        self._monitor.set_estado(EstadoMonitoreo.ENTRE_MEDICIONES)

        self._medicion.iniciar(
            modo         = self._modo_auto_activo(),
            intervalo    = self._spin_intervalo.value(),
            n_mediciones = self._spin_n_med.value(),
            t_inicial    = self._spin_t_ini.value(),
            t_final      = self._spin_t_fin.value(),
            paso         = self._spin_t_paso.value(),
        )

    @Slot()
    def _on_detener_secuencia(self):
        self._medicion.detener()

    @Slot(str, int)
    def _on_medicion_guardada(self, mid: str, n_flags: int):
        self._tab_viz.agregar_medicion(mid)
        self._lbl_progreso.setText(
            f"Última: {mid}  |  ⚠ con error: {n_flags}" if n_flags else f"Última: {mid}")

    @Slot(int)
    def _on_secuencia_ok(self, n_flags: int):
        self._secuencia_running = False
        self._reset_ui_auto()
        msg = (
            f"Secuencia completada.\n\nMediciones con error_flag: {n_flags}\n"
            "Revise las muestras marcadas en la tabla."
            if n_flags else "Secuencia completada sin errores."
        )
        QMessageBox.information(self, "Secuencia completada", msg)

    @Slot(str)
    def _on_secuencia_abortada(self, motivo: str):
        self._secuencia_running = False
        self._reset_ui_auto()
        QMessageBox.critical(self, "Secuencia abortada", motivo)

    def _reset_ui_auto(self):
        self._btn_iniciar_seq.setEnabled(self._sesion_activa)
        self._btn_detener_seq.setEnabled(False)
        self._btn_por_tiempo.setEnabled(True)
        self._btn_por_temp.setEnabled(True)
        self._lbl_progreso.setText("Secuencia detenida por el usuario.")
        self._monitor.set_estado(EstadoMonitoreo.REPOSO)

    # ══════════════════════════════════════════════════════════════════════════
    # STOP EMERGENCIA
    # ══════════════════════════════════════════════════════════════════════════

    @Slot()
    def _on_stop_emergencia(self):
        self._timer_inactividad.stop()
        if self._captura_thread is not None and self._captura_thread.isRunning():
            self._captura_thread.quit()
            self._captura_thread.wait(2000)
            self._captura_thread = None
            self._captura_worker = None
        if self._secuencia_running:
            self._medicion.detener()
            self._secuencia_running = False
        self._laser_running = False
        self._safe.activar()
        self._actualizar_ui_laser()
        self._reset_ui_auto()
        self._set_log("⚠ Stop emergencia — modo seguro activado")

    # ══════════════════════════════════════════════════════════════════════════
    # INACTIVIDAD
    # ══════════════════════════════════════════════════════════════════════════

    def _reiniciar_timer_inactividad(self):
        if self._laser_running and not self._secuencia_running:
            self._timer_inactividad.stop()
            self._timer_inactividad.start(INACTIVIDAD_AVISO_MS)

    @Slot()
    def _aviso_inactividad(self):
        if self._secuencia_running:
            return
        resp = QMessageBox.question(
            self, "¿Sigues usando el láser?",
            "No se ha detectado actividad en 1 minuto.\n\nEl láser se detendrá si no confirmas.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if resp == QMessageBox.Yes:
            self._reiniciar_timer_inactividad()
        else:
            self._on_stop_emergencia()

    # ══════════════════════════════════════════════════════════════════════════
    # OTROS SLOTS
    # ══════════════════════════════════════════════════════════════════════════

    def _set_log(self, texto: str):
        self._lbl_log.setText(texto)

    @Slot(str)
    def _on_seguridad_activada(self, dispositivo: str):
        self._laser_running = False
        self._actualizar_ui_laser()
        QMessageBox.critical(
            self, "Dispositivo crítico desconectado",
            f"No fue posible reconectar: {dispositivo}.\n\nEl láser fue puesto en modo seguro.",
        )

    @Slot()
    def _on_volver(self):
        if self._secuencia_running:
            resp = QMessageBox.question(
                self, "Secuencia en curso",
                "Hay una secuencia automática corriendo.\n\n¿Detenerla y salir?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                return
            self._on_stop_emergencia()
        if self._laser_running:
            resp = QMessageBox.question(
                self, "Láser encendido",
                "El láser sigue disparando. ¿Detenerlo antes de salir?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            )
            if resp == QMessageBox.Cancel:
                return
            if resp == QMessageBox.Yes:
                self._on_stop_emergencia()
        self._cerrar_recursos()
        self.volver.emit()
        self.close()

    # ══════════════════════════════════════════════════════════════════════════
    # CIERRE
    # ══════════════════════════════════════════════════════════════════════════

    def _cerrar_recursos(self):
        if self._cerrado:
            return
        self._cerrado = True
        self._timer_inactividad.stop()
        self._timer_monitor_laser.stop()
        if self._captura_thread is not None and self._captura_thread.isRunning():
            self._captura_thread.quit()
            self._captura_thread.wait(2000)
        self._medicion.detener()
        if self._laser.conectado:
            self._safe.activar()
        self._monitor.detener()
        if self._temp_thread.isRunning():
            self._temp.detener()
            self._temp_thread.quit()
            self._temp_thread.wait(3000)
        self._laser.desconectar()
        self._oscil.desconectar()

    def closeEvent(self, event):
        self._cerrar_recursos()
        event.accept()