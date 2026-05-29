"""
main_window.py
Ventana principal de la aplicación.
Instancia todos los módulos, conecta señales y gestiona la UI.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer, QThread, QObject, Signal, Slot
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QTabWidget,
    QRadioButton,
    QButtonGroup,
    QDoubleSpinBox,
    QSpinBox,
    QMessageBox,
    QFrame,
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

INACTIVIDAD_AVISO_MS = 60_000
INTERVALO_MIN_TIEMPO_S = (
    15.0  # mínimo razonable dado el tiempo de adquisición del osciloscopio
)


class _ManualCapturaWorker(QObject):
    """Worker para captura manual: corre en un QThread y no bloquea la GUI."""

    terminado = Signal(object)  # CapturaOscil | None

    def __init__(self, oscil: "OsciloscopioController"):
        super().__init__()
        self._oscil = oscil

    @Slot()
    def ejecutar(self):
        resultado = self._oscil.capturar()
        self.terminado.emit(resultado)


LED_VERDE = "#4caf50"
LED_AMARILLO = "#ffc107"
LED_ROJO = "#f44336"
LED_GRIS = "#555555"

ESTILO_LED = (
    "border-radius: 7px; min-width: 14px; max-width: 14px;"
    "min-height: 14px; max-height: 14px;"
)


def _led(color: str) -> QLabel:
    lbl = QLabel()
    lbl.setFixedSize(14, 14)
    lbl.setStyleSheet(f"background: {color}; {ESTILO_LED}")
    return lbl


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sistema de Medición Fotoacústica")
        self.setMinimumSize(960, 680)

        # ── Módulos ──────────────────────────────────────────────────────────
        self._laser = LaserController(self)
        self._oscil = OsciloscopioController(self)
        self._temp = TempWorker(TEMP_COM_PORT)
        self._temp_thread = QThread(self)
        self._temp.moveToThread(self._temp_thread)
        self._temp_thread.started.connect(self._temp.iniciar)

        self._store = Almacenamiento(self)
        self._safe = ModoSeguro(self._laser, self)
        self._monitor = MonitoreoConexion(
            self._laser, self._oscil, self._temp, self._safe, self
        )
        self._medicion = Medicion(
            self._laser,
            self._oscil,
            self._temp,
            self._store,
            self._safe,
            self._monitor,
            self,
        )

        # ── Estado interno ───────────────────────────────────────────────────
        self._modo_activo: str | None = None
        self._secuencia_corriendo = False

        # ── Thread de captura manual (evita bloquear la GUI) ─────────────────
        self._captura_thread: QThread | None = None
        self._captura_worker: _ManualCapturaWorker | None = None

        # ── Timer de inactividad (solo modo manual) ──────────────────────────
        self._timer_inactividad = QTimer(self)
        self._timer_inactividad.setSingleShot(True)
        self._timer_inactividad.timeout.connect(self._aviso_inactividad)

        self._construir_ui()
        self._conectar_signals()
        self._iniciar_app()

    # ══════════════════════════════════════════════════════════════════════════
    # CONSTRUCCIÓN DE UI
    # ══════════════════════════════════════════════════════════════════════════

    def _construir_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        root.addWidget(self._panel_conexiones())

        self._tabs = QTabWidget()
        self._tab_medicion = self._tab_medicion_widget()
        self._tab_viz = VisualizacionWidget(self._store, self)
        self._tabs.addTab(self._tab_medicion, "Medición")
        self._tabs.addTab(self._tab_viz, "Visualizar datos")
        root.addWidget(self._tabs, 1)

    # ── Panel de conexiones ───────────────────────────────────────────────────

    def _panel_conexiones(self) -> QWidget:
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(20)

        for attr, nombre in (
            ("_led_laser", "Láser"),
            ("_led_oscil", "Osciloscopio"),
            ("_led_esp32", "ESP32"),
        ):
            led = _led(LED_GRIS)
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

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        lay.addWidget(sep)

        self._leds_ds: list[QLabel] = []
        for i in range(4):
            led = _led(LED_GRIS)
            self._leds_ds.append(led)
            fila = QHBoxLayout()
            fila.setSpacing(4)
            fila.addWidget(led)
            fila.addWidget(QLabel(f"S{i+1}"))
            lay.addLayout(fila)

        lay.addStretch()

        self._lbl_temp_live = QLabel("—  °C")
        self._lbl_temp_live.setStyleSheet(
            "font-size: 15px; font-weight: bold; color: #00bfff;"
        )
        lay.addWidget(self._lbl_temp_live)

        return frame

    # ── Tab de medición ───────────────────────────────────────────────────────

    def _tab_medicion_widget(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(12)
        lay.addWidget(self._grupo_manual(), 1)
        lay.addWidget(self._grupo_auto(), 1)
        return w

    def _grupo_manual(self) -> QGroupBox:
        g = QGroupBox("Modo Manual")
        lay = QVBoxLayout(g)
        lay.setSpacing(10)

        self._lbl_manual_estado = QLabel("Inactivo")
        self._lbl_manual_estado.setAlignment(Qt.AlignCenter)
        self._lbl_manual_estado.setStyleSheet("color: #aaa; font-size: 12px;")

        self._btn_manual_iniciar = QPushButton("Iniciar")
        self._btn_manual_iniciar.setFixedHeight(38)

        self._btn_manual_guardar = QPushButton("Guardar medición")
        self._btn_manual_guardar.setFixedHeight(34)
        self._btn_manual_guardar.setEnabled(False)

        self._btn_manual_stop = QPushButton("⛔  Stop de emergencia")
        self._btn_manual_stop.setFixedHeight(34)
        self._btn_manual_stop.setEnabled(False)
        self._btn_manual_stop.setStyleSheet("color: #f44336; font-weight: bold;")

        lay.addWidget(self._lbl_manual_estado)
        lay.addWidget(self._btn_manual_iniciar)
        lay.addWidget(self._btn_manual_guardar)
        lay.addStretch()
        lay.addWidget(self._btn_manual_stop)
        return g

    def _grupo_auto(self) -> QGroupBox:
        g = QGroupBox("Modo Automático")
        lay = QVBoxLayout(g)
        lay.setSpacing(10)

        self._rb_tiempo = QRadioButton("Por tiempo")
        self._rb_temp = QRadioButton("Por temperatura")
        self._rb_tiempo.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self._rb_tiempo)
        grp.addButton(self._rb_temp)

        rb_row = QHBoxLayout()
        rb_row.addWidget(self._rb_tiempo)
        rb_row.addWidget(self._rb_temp)
        lay.addLayout(rb_row)

        # Config tiempo
        self._grp_tiempo = QGroupBox("Configuración — Tiempo")
        tl = QGridLayout(self._grp_tiempo)
        tl.addWidget(QLabel("Tomar medición cada"), 0, 0)
        self._spin_intervalo = QDoubleSpinBox()
        self._spin_intervalo.setRange(INTERVALO_MIN_TIEMPO_S, 3600)
        self._spin_intervalo.setValue(60)
        self._spin_intervalo.setSuffix("  s")
        tl.addWidget(self._spin_intervalo, 0, 1)
        tl.addWidget(QLabel("Número de mediciones"), 1, 0)
        self._spin_n_mediciones = QSpinBox()
        self._spin_n_mediciones.setRange(1, 9999)
        self._spin_n_mediciones.setValue(10)
        tl.addWidget(self._spin_n_mediciones, 1, 1)
        lay.addWidget(self._grp_tiempo)

        # Config temperatura
        self._grp_temp_cfg = QGroupBox("Configuración — Temperatura")
        tpc = QGridLayout(self._grp_temp_cfg)
        self._spin_t_ini = QDoubleSpinBox()
        self._spin_t_ini.setRange(0, 100)
        self._spin_t_ini.setValue(35)
        self._spin_t_ini.setSuffix(" °C")
        self._spin_t_fin = QDoubleSpinBox()
        self._spin_t_fin.setRange(0, 100)
        self._spin_t_fin.setValue(20)
        self._spin_t_fin.setSuffix(" °C")
        self._spin_t_paso = QDoubleSpinBox()
        self._spin_t_paso.setRange(0.1, 10)
        self._spin_t_paso.setValue(1.0)
        self._spin_t_paso.setSuffix(" °C")
        self._spin_t_paso.setSingleStep(0.1)
        tpc.addWidget(QLabel("T inicial"), 0, 0)
        tpc.addWidget(self._spin_t_ini, 0, 1)
        tpc.addWidget(QLabel("T final"), 1, 0)
        tpc.addWidget(self._spin_t_fin, 1, 1)
        tpc.addWidget(QLabel("Paso"), 2, 0)
        tpc.addWidget(self._spin_t_paso, 2, 1)
        self._grp_temp_cfg.setVisible(False)
        lay.addWidget(self._grp_temp_cfg)

        self._lbl_auto_progreso = QLabel("—")
        self._lbl_auto_progreso.setAlignment(Qt.AlignCenter)
        self._lbl_auto_progreso.setStyleSheet("color: #aaa; font-size: 12px;")
        lay.addWidget(self._lbl_auto_progreso)
        lay.addStretch()

        self._btn_auto_iniciar = QPushButton("Iniciar secuencia")
        self._btn_auto_iniciar.setFixedHeight(38)
        self._btn_auto_stop = QPushButton("⛔  Stop de emergencia")
        self._btn_auto_stop.setFixedHeight(34)
        self._btn_auto_stop.setEnabled(False)
        self._btn_auto_stop.setStyleSheet("color: #f44336; font-weight: bold;")
        lay.addWidget(self._btn_auto_iniciar)
        lay.addWidget(self._btn_auto_stop)
        return g

    # ══════════════════════════════════════════════════════════════════════════
    # CONEXIÓN DE SEÑALES
    # ══════════════════════════════════════════════════════════════════════════

    def _conectar_signals(self):
        # LEDs láser
        self._laser.led_verde.connect(lambda: self._set_led(self._led_laser, LED_VERDE))
        self._laser.led_amarillo.connect(
            lambda: self._set_led(self._led_laser, LED_AMARILLO)
        )
        self._laser.led_rojo.connect(lambda: self._set_led(self._led_laser, LED_ROJO))

        # LEDs láser — estado de reconexión desde el monitor
        self._monitor.laser_led_verde.connect(
            lambda: self._set_led(self._led_laser, LED_VERDE)
        )
        self._monitor.laser_led_amarillo.connect(
            lambda: self._set_led(self._led_laser, LED_AMARILLO)
        )
        self._monitor.laser_led_rojo.connect(
            lambda: self._set_led(self._led_laser, LED_ROJO)
        )

        # LEDs osciloscopio — estado de reconexión desde el monitor
        self._monitor.oscil_led_verde.connect(
            lambda: self._set_led(self._led_oscil, LED_VERDE)
        )
        self._monitor.oscil_led_amarillo.connect(
            lambda: self._set_led(self._led_oscil, LED_AMARILLO)
        )
        self._monitor.oscil_led_rojo.connect(
            lambda: self._set_led(self._led_oscil, LED_ROJO)
        )

        # LEDs osciloscopio
        self._oscil.led_verde.connect(lambda: self._set_led(self._led_oscil, LED_VERDE))
        self._oscil.led_amarillo.connect(
            lambda: self._set_led(self._led_oscil, LED_AMARILLO)
        )
        self._oscil.led_rojo.connect(lambda: self._set_led(self._led_oscil, LED_ROJO))

        # LEDs ESP32
        self._monitor.esp32_led_verde.connect(
            lambda: self._set_led(self._led_esp32, LED_VERDE)
        )
        self._monitor.esp32_led_amarillo.connect(
            lambda: self._set_led(self._led_esp32, LED_AMARILLO)
        )
        self._monitor.esp32_led_rojo.connect(
            lambda: self._set_led(self._led_esp32, LED_ROJO)
        )

        # LEDs sensores DS18B20
        self._monitor.ds_led_verde.connect(
            lambda i: self._set_led(self._leds_ds[i], LED_VERDE)
        )
        self._monitor.ds_led_amarillo.connect(
            lambda i: self._set_led(self._leds_ds[i], LED_AMARILLO)
        )
        self._monitor.ds_led_rojo.connect(
            lambda i: self._set_led(self._leds_ds[i], LED_ROJO)
        )

        # Temperatura en vivo
        self._temp.trigger.connect(self._on_temperatura)

        # Reconexión ESP32
        self._temp.desconectado.connect(self._on_esp32_desconectado)
        self._temp.conectado.connect(self._on_esp32_conectado)
        self._btn_reconectar_esp32.clicked.connect(self._reconectar_esp32)

        # Modo seguro por fallo de monitoreo
        self._monitor.seguridad_activada.connect(self._on_seguridad_activada)

        # Medición
        self._medicion.medicion_guardada.connect(self._on_medicion_guardada)
        self._medicion.secuencia_ok.connect(self._on_secuencia_ok)
        self._medicion.secuencia_abortada.connect(self._on_secuencia_abortada)

        # Almacenamiento
        self._store.sesion_lista.connect(self._on_sesion_lista)

        # Botones — manual
        self._btn_manual_iniciar.clicked.connect(self._on_manual_iniciar)
        self._btn_manual_guardar.clicked.connect(self._on_manual_guardar)
        self._btn_manual_stop.clicked.connect(self._on_stop_emergencia)

        # Botones — automático
        self._btn_auto_iniciar.clicked.connect(self._on_auto_iniciar)
        self._btn_auto_stop.clicked.connect(self._on_stop_emergencia)

        # Sub-modo automático — un solo radio basta para detectar el cambio
        self._rb_temp.toggled.connect(self._on_rb_modo)

        # Visualización reimportada
        self._tab_viz.sesion_reimportada.connect(
            lambda _: self._monitor.set_estado(EstadoMonitoreo.REPOSO)
        )

    # ══════════════════════════════════════════════════════════════════════════
    # INICIALIZACIÓN
    # ══════════════════════════════════════════════════════════════════════════

    def _iniciar_app(self):
        self._store.nueva_sesion()
        self._laser.conectar()
        self._oscil.conectar()
        self._temp_thread.start()
        self._monitor.iniciar()

    # ══════════════════════════════════════════════════════════════════════════
    # SLOTS — MODO MANUAL
    # ══════════════════════════════════════════════════════════════════════════

    def _on_manual_iniciar(self):
        if not self._laser.conectado or not self._oscil.conectado:
            QMessageBox.warning(
                self,
                "Dispositivos desconectados",
                "El láser y el osciloscopio deben estar conectados.",
            )
            return

        self._modo_activo = "manual"
        self._lbl_manual_estado.setText("Activo")
        self._lbl_manual_estado.setStyleSheet("color: #4caf50; font-weight: bold;")
        self._btn_manual_iniciar.setEnabled(False)
        self._btn_manual_guardar.setEnabled(True)
        self._btn_manual_stop.setEnabled(True)
        self._monitor.set_estado(EstadoMonitoreo.ENTRE_MEDICIONES)
        self._reiniciar_timer_inactividad()
        self._laser.start()

    def _on_manual_guardar(self):
        if self._captura_thread is not None and self._captura_thread.isRunning():
            return

        self._reiniciar_timer_inactividad()
        self._btn_manual_guardar.setEnabled(False)

        self._captura_worker = _ManualCapturaWorker(self._oscil)
        self._captura_thread = QThread(self)
        self._captura_worker.moveToThread(self._captura_thread)
        self._captura_thread.started.connect(self._captura_worker.ejecutar)
        self._captura_worker.terminado.connect(self._finalizar_captura_manual)
        self._captura_worker.terminado.connect(self._captura_thread.quit)
        self._captura_thread.finished.connect(self._captura_thread.deleteLater)
        self._captura_thread.start()

    @Slot(object)
    def _finalizar_captura_manual(self, captura):
        self._captura_worker = None
        self._captura_thread = None
        self._btn_manual_guardar.setEnabled(self._modo_activo == "manual")

        if captura is None:
            return

        temp, _, _ = self._temp.consultar()
        paquete = PaqueteMedicion(
            timestamp=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            temperatura=temp if temp is not None else 0.0,
            modo="manual",
            wfmpre=captura.wfmpre,
            raw_data=captura.raw_data,
            error_flag=1 if self._monitor.error_flag else 0,
        )
        mid = self._store.guardar(paquete)
        if mid:
            resp = QMessageBox.question(
                self,
                "Guardado",
                f"Medición {mid} guardada.\n¿Desea visualizarla?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if resp == QMessageBox.Yes:
                self._tab_viz.cargar_sesion_activa()
                self._tabs.setCurrentIndex(1)

    # ══════════════════════════════════════════════════════════════════════════
    # SLOTS — MODO AUTOMÁTICO
    # ══════════════════════════════════════════════════════════════════════════

    def _on_rb_modo(self, por_temp: bool):
        self._grp_temp_cfg.setVisible(por_temp)
        self._grp_tiempo.setVisible(not por_temp)

    def _on_auto_iniciar(self):
        por_temp = self._rb_temp.isChecked()

        if not self._laser.conectado or not self._oscil.conectado:
            QMessageBox.warning(
                self,
                "Dispositivos desconectados",
                "El láser y el osciloscopio deben estar conectados.",
            )
            return

        if por_temp and not self._temp.esta_conectado():
            QMessageBox.warning(
                self,
                "ESP32 desconectado",
                "El módulo de temperatura debe estar conectado para este modo.",
            )
            return

        aviso = QMessageBox.warning(
            self,
            "Confirmar inicio",
            "Está a punto de iniciar el láser.\n\n"
            "Verifique que tenga las protecciones necesarias y esté\n"
            "correctamente configurado. Una vez iniciado, deberá\n"
            "detenerlo completamente para cambiar parámetros.",
            QMessageBox.Ok | QMessageBox.Cancel,
        )
        if aviso != QMessageBox.Ok:
            return

        modo = "temperatura" if por_temp else "tiempo"
        self._modo_activo = modo
        self._secuencia_corriendo = True
        self._btn_auto_iniciar.setEnabled(False)
        self._btn_auto_stop.setEnabled(True)
        self._rb_tiempo.setEnabled(False)
        self._rb_temp.setEnabled(False)
        self._lbl_auto_progreso.setText("Secuencia en curso…")
        self._monitor.set_estado(EstadoMonitoreo.ENTRE_MEDICIONES)

        self._medicion.iniciar(
            modo=modo,
            intervalo=self._spin_intervalo.value(),
            n_mediciones=self._spin_n_mediciones.value(),
            t_inicial=self._spin_t_ini.value(),
            t_final=self._spin_t_fin.value(),
            paso=self._spin_t_paso.value(),
        )

    # ══════════════════════════════════════════════════════════════════════════
    # SLOTS — SEÑALES DE MEDICIÓN
    # ══════════════════════════════════════════════════════════════════════════

    def _on_medicion_guardada(self, mid: str, n_flags: int):
        self._tab_viz.agregar_medicion(mid)
        self._lbl_auto_progreso.setText(
            f"Última: {mid}  |  ⚠️ con error: {n_flags}"
            if n_flags
            else f"Última: {mid}"
        )

    def _on_secuencia_ok(self, n_flags: int):
        self._secuencia_corriendo = False
        self._reset_ui_auto()
        msg = (
            f"Secuencia completada.\n\n"
            f"Mediciones con error_flag: {n_flags}\n"
            "Revise las muestras marcadas en la tabla."
            if n_flags
            else "Secuencia completada sin errores."
        )
        QMessageBox.information(self, "Secuencia completada", msg, QMessageBox.Ok)
        resp = QMessageBox.question(
            self,
            "Visualizar",
            "¿Desea visualizar los resultados?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if resp == QMessageBox.Yes:
            self._tab_viz.cargar_sesion_activa()
            self._tabs.setCurrentIndex(1)

    def _on_secuencia_abortada(self, motivo: str):
        self._secuencia_corriendo = False
        self._reset_ui_auto()
        QMessageBox.critical(self, "Secuencia abortada", motivo)

    def _reset_ui_auto(self):
        self._btn_auto_iniciar.setEnabled(True)
        self._btn_auto_stop.setEnabled(False)
        self._rb_tiempo.setEnabled(True)
        self._rb_temp.setEnabled(True)
        self._lbl_auto_progreso.setText("—")
        self._monitor.set_estado(EstadoMonitoreo.REPOSO)

    # ══════════════════════════════════════════════════════════════════════════
    # STOP DE EMERGENCIA
    # ══════════════════════════════════════════════════════════════════════════

    def _on_stop_emergencia(self):
        self._timer_inactividad.stop()
        if self._captura_thread is not None and self._captura_thread.isRunning():
            self._captura_thread.quit()
            self._captura_thread.wait(2000)
            self._captura_worker = None
            self._captura_thread = None
        if self._secuencia_corriendo:
            self._medicion.detener()
            self._secuencia_corriendo = False
        self._safe.activar()
        self._reset_ui_manual()
        self._reset_ui_auto()

    def _reset_ui_manual(self):
        self._modo_activo = None
        self._lbl_manual_estado.setText("Inactivo")
        self._lbl_manual_estado.setStyleSheet("color: #aaa; font-size: 12px;")
        self._btn_manual_iniciar.setEnabled(True)
        self._btn_manual_guardar.setEnabled(False)
        self._btn_manual_stop.setEnabled(False)

    # ══════════════════════════════════════════════════════════════════════════
    # RECONEXIÓN ESP32
    # ══════════════════════════════════════════════════════════════════════════

    @Slot()
    def _on_esp32_desconectado(self):
        self._set_led(self._led_esp32, LED_ROJO)
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
    # INACTIVIDAD — MODO MANUAL
    # ══════════════════════════════════════════════════════════════════════════

    def _reiniciar_timer_inactividad(self):
        self._timer_inactividad.stop()
        self._timer_inactividad.start(INACTIVIDAD_AVISO_MS)

    def _aviso_inactividad(self):
        resp = QMessageBox.question(
            self,
            "¿Sigues usando el láser?",
            "No se ha detectado actividad en 1 minuto.\n\n"
            "El láser se detendrá si no confirmas.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if resp == QMessageBox.Yes:
            self._reiniciar_timer_inactividad()
        else:
            self._on_stop_emergencia()
            QMessageBox.information(
                self,
                "Láser detenido",
                "El láser fue detenido por inactividad y puesto en modo seguro.",
            )

    # ══════════════════════════════════════════════════════════════════════════
    # OTROS SLOTS
    # ══════════════════════════════════════════════════════════════════════════

    def _on_temperatura(self, temp: float):
        self._lbl_temp_live.setText(f"{temp:.2f}  °C")

    def _on_sesion_lista(self, sid: str):
        self.setWindowTitle(f"Sistema de Medición Fotoacústica  —  {sid}")

    def _on_seguridad_activada(self, dispositivo: str):
        QMessageBox.critical(
            self,
            "Dispositivo crítico desconectado",
            f"No fue posible reconectar: {dispositivo}.\n\n"
            "El láser fue puesto en modo seguro y la secuencia fue detenida.",
        )

    @staticmethod
    def _set_led(led: QLabel, color: str):
        led.setStyleSheet(f"background: {color}; {ESTILO_LED}")

    # ══════════════════════════════════════════════════════════════════════════
    # CIERRE
    # ══════════════════════════════════════════════════════════════════════════

    def closeEvent(self, event):
        self._timer_inactividad.stop()
        if self._captura_thread is not None and self._captura_thread.isRunning():
            self._captura_thread.quit()
            self._captura_thread.wait(2000)
        self._medicion.detener()
        self._safe.activar()
        self._monitor.detener()
        if self._temp_thread.isRunning():
            self._temp.detener()
            self._temp_thread.quit()
            self._temp_thread.wait(3000)
        self._laser.desconectar()
        self._oscil.desconectar()
        event.accept()