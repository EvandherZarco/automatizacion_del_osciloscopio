"""
ventana_laser.py
Ventana de control exclusivo del láser EKSPLA NL303HT-10-SH.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QPushButton, QSpinBox, QDoubleSpinBox, QFrame, QMessageBox,
)

from app.laser.control_laser import LaserController
from app.modo_seguro.modo_seguro import ModoSeguro
from app.gui.theme import (
    APP_STYLESHEET, LED_VERDE, LED_AMARILLO, LED_ROJO, LED_GRIS,
    make_led, set_led, set_btn_activo, chip_log,
)

_MONITOREO_INTERVALO_MS = 10_000


class VentanaLaser(QMainWindow):

    volver = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Láser — EKSPLA NL303HT-10-SH")
        self.setMinimumSize(820, 580)
        self.setStyleSheet(APP_STYLESHEET)

        self._laser = LaserController(self)
        self._safe  = ModoSeguro(self._laser, self)

        self._laser_running = False
        self._cerrado       = False
        self._output_sel    = "E Adjust"
        self._burst_sel     = "Continuous"

        self._timer_monitor = QTimer(self)
        self._timer_monitor.setInterval(_MONITOREO_INTERVALO_MS)
        self._timer_monitor.timeout.connect(self._actualizar_monitoreo)

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

        contenido = QWidget()
        lay = QHBoxLayout(contenido)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(12)
        lay.addWidget(self._panel_izquierdo(), 0)
        lay.addWidget(self._panel_parametros(), 1)
        root.addWidget(contenido, 1)

        root.addWidget(self._bottombar())

    # ── Topbar ────────────────────────────────────────────────────────────────

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

    # ── Panel izquierdo ───────────────────────────────────────────────────────

    def _panel_izquierdo(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(210)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        lay.addWidget(self._indicador_estado())

        fila = QHBoxLayout()
        self._btn_iniciar = QPushButton("▶  Iniciar")
        self._btn_iniciar.setFixedHeight(38)
        self._btn_iniciar.setEnabled(False)
        self._btn_detener = QPushButton("□  Detener")
        self._btn_detener.setFixedHeight(38)
        self._btn_detener.setEnabled(False)
        fila.addWidget(self._btn_iniciar)
        fila.addWidget(self._btn_detener)
        lay.addLayout(fila)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #2e2e2e;")
        lay.addWidget(sep)

        lay.addWidget(self._panel_monitoreo())
        lay.addStretch()
        return w

    def _indicador_estado(self) -> QGroupBox:
        g = QGroupBox("Estado del láser")
        lay = QVBoxLayout(g)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(6)

        self._circulo = QLabel("⊗")
        self._circulo.setAlignment(Qt.AlignCenter)
        self._circulo.setFixedSize(80, 80)
        self._circulo.setStyleSheet(
            "background: #2a1a1a; border: 2px solid #f44336;"
            "border-radius: 40px; color: #f44336; font-size: 28px;"
        )
        self._lbl_estado_texto = QLabel("STOP")
        self._lbl_estado_texto.setAlignment(Qt.AlignCenter)
        self._lbl_estado_texto.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #f44336;"
        )
        lay.addWidget(self._circulo, 0, Qt.AlignCenter)
        lay.addWidget(self._lbl_estado_texto, 0, Qt.AlignCenter)
        return g

    def _panel_monitoreo(self) -> QGroupBox:
        g = QGroupBox("Monitoreo")
        lay = QVBoxLayout(g)
        lay.setSpacing(6)
        self._card_t_actual = self._metric_card("T agua actual", "—", "°C (solo lectura)")
        self._card_t_obj    = self._metric_card("T agua objetivo", "—", "°C")
        self._card_pulsos   = self._metric_card("Contador de pulsos", "—",
                                                "disparos totales (solo lectura)")
        lay.addWidget(self._card_t_actual)
        lay.addWidget(self._card_t_obj)
        lay.addWidget(self._card_pulsos)
        return g

    def _metric_card(self, titulo: str, valor: str, hint: str) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: #252525; border-radius: 6px;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(2)
        lbl_t = QLabel(titulo)
        lbl_t.setStyleSheet("color: #777; font-size: 10px; background: transparent;")
        lbl_v = QLabel(valor)
        lbl_v.setStyleSheet("font-size: 18px; font-weight: bold; background: transparent;")
        lbl_h = QLabel(hint)
        lbl_h.setStyleSheet("color: #555; font-size: 9px; background: transparent;")
        lay.addWidget(lbl_t)
        lay.addWidget(lbl_v)
        lay.addWidget(lbl_h)
        w._lbl_valor = lbl_v
        return w

    # ── Panel de parámetros ───────────────────────────────────────────────────

    def _panel_parametros(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self._banner_bloqueo = QLabel("🔒  Detén el láser para modificar parámetros")
        self._banner_bloqueo.setAlignment(Qt.AlignCenter)
        self._banner_bloqueo.setStyleSheet(
            "background: #2a1f00; color: #ffc107; border: 1px solid #3a2f00;"
            "border-radius: 6px; padding: 8px; font-size: 12px;"
        )
        self._banner_bloqueo.setVisible(False)
        lay.addWidget(self._banner_bloqueo)

        encabezado = QLabel("Parámetros del láser")
        encabezado.setStyleSheet("font-size: 13px; font-weight: bold; color: #aaa;")
        lay.addWidget(encabezado)

        # Output level
        lay.addWidget(self._sep_label("Output level"))
        fila_ol = QHBoxLayout()
        fila_ol.setSpacing(6)
        self._btn_e_off = QPushButton("E OFF")
        self._btn_e_adj = QPushButton("E Adjust")
        self._btn_e_max = QPushButton("E Max")
        for btn in (self._btn_e_off, self._btn_e_adj, self._btn_e_max):
            btn.setFixedHeight(34)
            fila_ol.addWidget(btn)
        lay.addLayout(fila_ol)

        # Burst mode
        lay.addWidget(self._sep_label("Burst mode"))
        fila_bm = QHBoxLayout()
        fila_bm.setSpacing(6)
        self._btn_continuous = QPushButton("Continuous")
        self._btn_burst      = QPushButton("Burst")
        self._btn_trigger    = QPushButton("Trigger")
        for btn in (self._btn_continuous, self._btn_burst, self._btn_trigger):
            btn.setFixedHeight(34)
            fila_bm.addWidget(btn)
        lay.addLayout(fila_bm)

        # Burst length + Cooling T
        campos = QHBoxLayout()
        campos.setSpacing(14)

        col_bl = QVBoxLayout()
        col_bl.addWidget(self._sep_label("Burst length"))
        self._spin_burst_len = QSpinBox()
        self._spin_burst_len.setRange(1, 30000)
        self._spin_burst_len.setValue(1)
        self._spin_burst_len.setEnabled(False)
        lbl_bl_h = QLabel("pulsos (1–30 000)")
        lbl_bl_h.setStyleSheet("color: #555; font-size: 10px;")
        col_bl.addWidget(self._spin_burst_len)
        col_bl.addWidget(lbl_bl_h)

        col_cool = QVBoxLayout()
        col_cool.addWidget(self._sep_label("Set cooling T"))
        self._spin_cooling = QDoubleSpinBox()
        self._spin_cooling.setRange(10.0, 50.0)
        self._spin_cooling.setSingleStep(0.1)
        self._spin_cooling.setDecimals(1)
        self._spin_cooling.setValue(30.0)
        lbl_cool_h = QLabel("°C (10.0–50.0)")
        lbl_cool_h.setStyleSheet("color: #555; font-size: 10px;")
        col_cool.addWidget(self._spin_cooling)
        col_cool.addWidget(lbl_cool_h)

        campos.addLayout(col_bl)
        campos.addLayout(col_cool)
        lay.addLayout(campos)

        # EO delay
        lay.addWidget(self._sep_label("Adj. EO delay"))
        self._spin_eo = QSpinBox()
        self._spin_eo.setRange(800, 8000)
        self._spin_eo.setValue(3800)
        lbl_eo_h = QLabel("µs (800–8 000)  —  modo seguro = 3800")
        lbl_eo_h.setStyleSheet("color: #555; font-size: 10px;")
        lay.addWidget(self._spin_eo)
        lay.addWidget(lbl_eo_h)

        lay.addStretch()

        # Aplicar
        fila_ap = QHBoxLayout()
        fila_ap.setSpacing(12)
        self._btn_aplicar = QPushButton("✧  Aplicar parámetros")
        self._btn_aplicar.setFixedHeight(38)
        self._btn_aplicar.setEnabled(False)
        lbl_ap_h = QLabel("Envía todos los cambios al láser")
        lbl_ap_h.setStyleSheet("color: #555; font-size: 11px;")
        fila_ap.addWidget(self._btn_aplicar, 1)
        fila_ap.addWidget(lbl_ap_h)
        lay.addLayout(fila_ap)
        return w

    def _sep_label(self, texto: str) -> QLabel:
        lbl = QLabel(texto)
        lbl.setStyleSheet("color: #888; font-size: 11px; text-transform: uppercase;")
        return lbl

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

    # ══════════════════════════════════════════════════════════════════════════
    # SEÑALES
    # ══════════════════════════════════════════════════════════════════════════

    def _conectar_signals(self):
        self._laser.led_verde.connect(self._on_laser_conectado)
        self._laser.led_rojo.connect(self._on_laser_desconectado)
        self._laser.led_amarillo.connect(lambda: set_led(self._led_conn, LED_AMARILLO))
        self._laser.cmd_ok.connect(self._set_log)
        self._laser.error.connect(self._set_log)

        self._btn_volver.clicked.connect(self._on_volver)
        self._btn_conectar.clicked.connect(self._on_toggle_conexion)
        self._btn_iniciar.clicked.connect(self._on_iniciar)
        self._btn_detener.clicked.connect(self._on_detener)

        self._btn_e_off.clicked.connect(lambda: self._sel_output("E OFF"))
        self._btn_e_adj.clicked.connect(lambda: self._sel_output("E Adjust"))
        self._btn_e_max.clicked.connect(lambda: self._sel_output("E Max"))

        self._btn_continuous.clicked.connect(lambda: self._sel_burst("Continuous"))
        self._btn_burst.clicked.connect(lambda: self._sel_burst("Burst"))
        self._btn_trigger.clicked.connect(lambda: self._sel_burst("Trigger"))

        self._btn_aplicar.clicked.connect(self._on_aplicar)
        self._btn_stop_emergencia.clicked.connect(self._on_stop_emergencia)

        self._sel_output("E Adjust")
        self._sel_burst("Continuous")

    # ══════════════════════════════════════════════════════════════════════════
    # SLOTS
    # ══════════════════════════════════════════════════════════════════════════

    @Slot()
    def _on_toggle_conexion(self):
        if self._laser.conectado:
            self._timer_monitor.stop()
            self._laser.desconectar()
            self._btn_conectar.setText("Conectar")
            self._btn_iniciar.setEnabled(False)
            self._btn_aplicar.setEnabled(False)
        else:
            if self._laser.conectar():
                self._btn_conectar.setText("Desconectar")
                self._actualizar_monitoreo()
                self._timer_monitor.start()

    @Slot()
    def _on_laser_conectado(self):
        set_led(self._led_conn, LED_VERDE)
        self._lbl_nombre.setText("NL303HT-10-SH")
        self._chip_estado.setText("Conectado — COM10")
        self._chip_estado.setStyleSheet(
            "background: #1a3a1a; color: #4caf50; font-size: 11px;"
            "border: 1px solid #2a5a2a; border-radius: 4px; padding: 3px 10px;"
        )
        self._btn_iniciar.setEnabled(not self._laser_running)
        self._btn_aplicar.setEnabled(not self._laser_running)

    @Slot()
    def _on_laser_desconectado(self):
        set_led(self._led_conn, LED_ROJO)
        self._lbl_nombre.setText("Sin conexión")
        self._chip_estado.setText("Desconectado")
        self._chip_estado.setStyleSheet(
            "background: #2a2a2a; color: #666; font-size: 11px;"
            "border: 1px solid #333; border-radius: 4px; padding: 3px 10px;"
        )
        self._btn_iniciar.setEnabled(False)
        self._btn_aplicar.setEnabled(False)

    @Slot()
    def _on_iniciar(self):
        resp = QMessageBox.warning(
            self, "Encender el láser",
            "El láser está a punto de disparar.\n\n"
            "Verifique que las protecciones estén en su lugar antes de continuar.",
            QMessageBox.Ok | QMessageBox.Cancel,
        )
        if resp != QMessageBox.Ok:
            return
        if self._laser.start():
            self._laser_running = True
            self._actualizar_estado_ui()

    @Slot()
    def _on_detener(self):
        if self._laser.stop():
            self._laser_running = False
            self._actualizar_estado_ui()

    def _actualizar_estado_ui(self):
        c = self._laser_running
        self._circulo.setText("⚡" if c else "⊗")
        self._circulo.setStyleSheet(
            f"background: {'#1a3a1a' if c else '#2a1a1a'};"
            f"border: 2px solid {'#4caf50' if c else '#f44336'};"
            "border-radius: 40px; font-size: 28px;"
            f"color: {'#4caf50' if c else '#f44336'};"
        )
        self._lbl_estado_texto.setText("RUN" if c else "STOP")
        self._lbl_estado_texto.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {'#4caf50' if c else '#f44336'};"
        )
        self._banner_bloqueo.setVisible(c)
        self._btn_iniciar.setEnabled(not c and self._laser.conectado)
        self._btn_detener.setEnabled(c)
        self._btn_aplicar.setEnabled(not c and self._laser.conectado)
        for w in (self._btn_e_off, self._btn_e_adj, self._btn_e_max,
                  self._btn_continuous, self._btn_burst, self._btn_trigger,
                  self._spin_burst_len, self._spin_cooling, self._spin_eo):
            w.setEnabled(not c)
        if not c:
            self._sel_burst(self._burst_sel)

    def _sel_output(self, modo: str):
        self._output_sel = modo
        mapa = {
            "E OFF":    (self._btn_e_off, "amber"),
            "E Adjust": (self._btn_e_adj, "azul"),
            "E Max":    (self._btn_e_max, "verde"),
        }
        for m, (btn, est) in mapa.items():
            set_btn_activo(btn, m == modo, est)

    def _sel_burst(self, modo: str):
        self._burst_sel = modo
        mapa = {
            "Continuous": self._btn_continuous,
            "Burst":      self._btn_burst,
            "Trigger":    self._btn_trigger,
        }
        for m, btn in mapa.items():
            set_btn_activo(btn, m == modo, "azul")
        self._spin_burst_len.setEnabled(modo != "Continuous" and not self._laser_running)

    @Slot()
    def _on_aplicar(self):
        self._laser.set_output_level(self._output_sel)
        self._laser.set_burst_mode(self._burst_sel)
        if self._burst_sel != "Continuous":
            self._laser.set_burst_length(self._spin_burst_len.value())
        self._laser.set_cooling_temp(self._spin_cooling.value())
        self._laser.set_eo_delay(self._spin_eo.value())
        estado = "RUN" if self._laser_running else "STOP"
        self._set_log(f"State → {estado} · Parámetros {'bloqueados' if self._laser_running else 'desbloqueados'}")

    @Slot()
    def _on_stop_emergencia(self):
        self._laser_running = False
        self._safe.activar()
        self._actualizar_estado_ui()
        self._set_log("⚠ Stop emergencia — modo seguro activado")

    @Slot()
    def _on_volver(self):
        if self._laser_running:
            resp = QMessageBox.question(
                self, "Láser encendido",
                "El láser sigue disparando.\n\n¿Deseas detenerlo antes de salir?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            )
            if resp == QMessageBox.Cancel:
                return
            if resp == QMessageBox.Yes:
                self._on_stop_emergencia()
        self._cerrar_recursos()
        self.volver.emit()
        self.close()

    def _actualizar_monitoreo(self):
        t = self._laser.read_cooling_temp()
        if t is not None:
            self._card_t_actual._lbl_valor.setText(f"{t:.1f}")
        p = self._laser.read_pulse_counter()
        if p is not None:
            self._card_pulsos._lbl_valor.setText(f"{p:,}")
        self._card_t_obj._lbl_valor.setText(f"{self._spin_cooling.value():.1f}")

    def _set_log(self, texto: str):
        self._lbl_log.setText(texto)

    def _cerrar_recursos(self):
        if self._cerrado:
            return
        self._cerrado = True
        self._timer_monitor.stop()
        if self._laser_running:
            self._safe.activar()
        if self._laser.conectado:
            self._laser.desconectar()

    def closeEvent(self, event):
        self._cerrar_recursos()
        event.accept()