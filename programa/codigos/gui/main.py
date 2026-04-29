"""
Sistema Fotoacústico — ICAT, UNAM
GUI principal · Tesis Ingeniería Mecatrónica · Zarco
"""
from __future__ import annotations
import sys, time, csv, os
from datetime import datetime
from pathlib import Path

import numpy as np
import serial
import vxi11
import pyqtgraph as pg

from PySide6 import QtCore, QtGui, QtWidgets

# ─────────────────────────────────────────────────────────────
#  CONSTANTES
# ─────────────────────────────────────────────────────────────
SCOPE_IP      = "192.168.1.100"
LASER_PORT    = "COM4"
LASER_BAUD    = 19200
SCOPE_CHANNEL = "CH2"
CSV_DIR       = Path("mediciones")

DARK_BG     = "#0d1117"
PANEL_BG    = "#161b22"
BORDER      = "#30363d"
CYAN        = "#58c7e0"
GREEN       = "#3fb950"
AMBER       = "#f0b429"
RED         = "#f85149"
TEXT        = "#e6edf3"
TEXT_DIM    = "#8b949e"

# ─────────────────────────────────────────────────────────────
#  COMUNICACIÓN LASER (serial, protocolo NL)
# ─────────────────────────────────────────────────────────────
class LaserSerial:
    def __init__(self, port: str, baud: int = 19200):
        self.port = port
        self.baud = baud
        self.ser: serial.Serial | None = None

    def open(self):
        self.ser = serial.Serial(
            self.port, self.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.5,
        )
        self.ser.reset_input_buffer()

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()

    def _frame(self, cmd: str) -> bytes:
        return f"|[NL:{cmd}\\PC]|".encode("ascii")

    def send(self, cmd: str, wait: float = 0.35) -> str:
        assert self.ser
        self.ser.reset_input_buffer()
        self.ser.write(self._frame(cmd))
        self.ser.flush()
        time.sleep(wait)
        raw = self.ser.read(self.ser.in_waiting)
        return raw.decode("ascii", errors="replace").strip()

    def say(self)   -> str: return self.send("SAY")
    def start(self) -> str: return self.send("START")
    def stop(self)  -> str: return self.send("STOP", wait=0.5)


# ─────────────────────────────────────────────────────────────
#  WORKER — secuencia de medición en hilo separado
# ─────────────────────────────────────────────────────────────
class MeasurementWorker(QtCore.QObject):
    log     = QtCore.Signal(str, str)          # (mensaje, nivel)
    waveform_ready = QtCore.Signal(object, object, float, str)  # tiempo, voltaje, pico_V, csv_path
    finished = QtCore.Signal(bool)             # True = éxito

    def __init__(self, laser_port: str, scope_ip: str, channel: str):
        super().__init__()
        self.laser_port = laser_port
        self.scope_ip   = scope_ip
        self.channel    = channel

    @QtCore.Slot()
    def run(self):
        laser = LaserSerial(self.laser_port)
        scope = None
        success = False

        try:
            # ── 1. Conectar láser ──────────────────────────────
            self.log.emit("Conectando al láser...", "info")
            laser.open()
            rx = laser.say()
            if "READY" not in rx:
                raise RuntimeError(f"Láser no respondió READY. RX: {rx!r}")
            self.log.emit(f"Láser listo  ✓  ({rx.strip()})", "ok")

            # ── 2. Conectar osciloscopio ───────────────────────
            self.log.emit("Conectando al osciloscopio...", "info")
            scope = vxi11.Instrument(self.scope_ip)
            idn = scope.ask("*IDN?")
            self.log.emit(f"Osciloscopio listo  ✓  ({idn.split(',')[1]})", "ok")

            # ── 3. Encender láser ──────────────────────────────
            self.log.emit("Encendiendo láser (START)...", "info")
            rx = laser.start()
            self.log.emit(f"Láser encendido  ✓  ({rx.strip()})", "ok")
            time.sleep(0.5)          # pequeña pausa para estabilizar señal

            # ── 4. Capturar forma de onda ──────────────────────
            self.log.emit(f"Capturando {self.channel}...", "info")
            scope.write(f"DATA:SOURCE {self.channel}")
            scope.write("DATA:ENC RIBINARY")
            scope.write("DATA:WIDTH 1")

            xincr  = float(scope.ask("WFMPRE:XINCR?"))
            ymult  = float(scope.ask("WFMPRE:YMULT?"))
            yoff   = float(scope.ask("WFMPRE:YOFF?"))
            yzero  = float(scope.ask("WFMPRE:YZERO?"))
            xzero  = float(scope.ask("WFMPRE:XZERO?"))
            pt_off = int(scope.ask("WFMPRE:PT_OFF?"))

            raw      = scope.ask_raw(b"CURVE?")
            n_digits = int(chr(raw[1]))
            data     = np.frombuffer(raw[2 + n_digits:], dtype=np.int8)

            voltaje = (data.astype(float) - yoff) * ymult + yzero
            tiempo  = xzero + (np.arange(len(data)) - pt_off) * xincr

            pico_idx = int(np.argmax(np.abs(voltaje)))
            pico_V   = voltaje[pico_idx]
            self.log.emit(
                f"Captura OK  ✓  {len(data)} muestras · pico {pico_V*1000:.2f} mV", "ok"
            )

            # ── 5. Apagar láser ────────────────────────────────
            self.log.emit("Apagando láser (STOP)...", "info")
            laser.stop()
            self.log.emit("Láser apagado  ✓", "ok")

            # ── 6. Guardar CSV ─────────────────────────────────
            CSV_DIR.mkdir(exist_ok=True)
            ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_path = CSV_DIR / f"medicion_{ts}.csv"

            with open(csv_path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["# Sistema Fotoacústico ICAT-UNAM"])
                w.writerow(["# Fecha", datetime.now().isoformat()])
                w.writerow(["# Canal", self.channel])
                w.writerow(["# Muestras", len(data)])
                w.writerow(["# Pico_V", f"{pico_V:.6f}"])
                w.writerow(["# xincr_s", f"{xincr:.6e}"])
                w.writerow(["# ymult_V", f"{ymult:.6e}"])
                w.writerow(["tiempo_s", "voltaje_V"])
                for t, v in zip(tiempo, voltaje):
                    w.writerow([f"{t:.9e}", f"{v:.6e}"])

            self.log.emit(f"CSV guardado  ✓  {csv_path.name}", "ok")
            self.waveform_ready.emit(tiempo, voltaje, pico_V, str(csv_path))
            success = True

        except Exception as e:
            self.log.emit(f"ERROR: {e}", "error")
            try:
                laser.stop()
            except Exception:
                pass
        finally:
            laser.close()
            self.finished.emit(success)


# ─────────────────────────────────────────────────────────────
#  VENTANA PRINCIPAL
# ─────────────────────────────────────────────────────────────
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sistema Fotoacústico · ICAT-UNAM")
        self.setMinimumSize(1100, 700)
        self._apply_stylesheet()
        self._build_ui()
        self._thread: QtCore.QThread | None = None

    # ── Estilos ───────────────────────────────────────────────
    def _apply_stylesheet(self):
        self.setStyleSheet(f"""
        QMainWindow, QWidget {{
            background: {DARK_BG};
            color: {TEXT};
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 13px;
        }}
        QLabel#title {{
            font-size: 18px;
            font-weight: bold;
            color: {CYAN};
            letter-spacing: 2px;
        }}
        QLabel#subtitle {{
            font-size: 11px;
            color: {TEXT_DIM};
            letter-spacing: 1px;
        }}
        QGroupBox {{
            border: 1px solid {BORDER};
            border-radius: 6px;
            margin-top: 12px;
            padding: 8px;
            background: {PANEL_BG};
            font-size: 11px;
            color: {TEXT_DIM};
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 10px;
            top: -1px;
            color: {CYAN};
            font-size: 11px;
            letter-spacing: 1px;
        }}
        QLineEdit {{
            background: {DARK_BG};
            border: 1px solid {BORDER};
            border-radius: 4px;
            padding: 5px 8px;
            color: {TEXT};
        }}
        QLineEdit:focus {{
            border-color: {CYAN};
        }}
        QPushButton#btn_medir {{
            background: {CYAN};
            color: {DARK_BG};
            font-size: 15px;
            font-weight: bold;
            border: none;
            border-radius: 8px;
            padding: 14px 0px;
            letter-spacing: 2px;
        }}
        QPushButton#btn_medir:hover {{
            background: #7dd5ec;
        }}
        QPushButton#btn_medir:disabled {{
            background: {BORDER};
            color: {TEXT_DIM};
        }}
        QPushButton#btn_clear {{
            background: transparent;
            color: {TEXT_DIM};
            border: 1px solid {BORDER};
            border-radius: 4px;
            padding: 4px 12px;
            font-size: 11px;
        }}
        QPushButton#btn_clear:hover {{
            border-color: {TEXT_DIM};
            color: {TEXT};
        }}
        QTextEdit {{
            background: {DARK_BG};
            border: 1px solid {BORDER};
            border-radius: 4px;
            color: {TEXT};
            font-family: 'Consolas', monospace;
            font-size: 12px;
        }}
        QLabel#status_ok  {{ color: {GREEN}; font-weight: bold; }}
        QLabel#status_err {{ color: {RED};   font-weight: bold; }}
        QLabel#status_run {{ color: {AMBER}; font-weight: bold; }}
        QLabel#peak_val   {{
            color: {CYAN};
            font-size: 26px;
            font-weight: bold;
        }}
        QLabel#peak_lbl   {{
            color: {TEXT_DIM};
            font-size: 11px;
            letter-spacing: 1px;
        }}
        """)

    # ── UI ────────────────────────────────────────────────────
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        # Header
        hdr = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("SISTEMA FOTOACÚSTICO")
        title.setObjectName("title")
        sub   = QtWidgets.QLabel("ICAT · UNAM · Ing. Mecatrónica")
        sub.setObjectName("subtitle")
        sub.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        hdr.addWidget(title)
        hdr.addStretch()
        hdr.addWidget(sub)
        root.addLayout(hdr)

        # Separador
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet(f"color: {BORDER};")
        root.addWidget(line)

        # Cuerpo principal
        body = QtWidgets.QHBoxLayout()
        body.setSpacing(12)
        root.addLayout(body, stretch=1)

        # ── Panel izquierdo ────────────────────────────────────
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(10)
        body.addLayout(left, stretch=0)

        # Conexión láser
        grp_laser = QtWidgets.QGroupBox("LÁSER")
        fl = QtWidgets.QFormLayout(grp_laser)
        fl.setSpacing(6)
        self.inp_com = QtWidgets.QLineEdit(LASER_PORT)
        self.inp_com.setFixedWidth(90)
        fl.addRow("Puerto COM:", self.inp_com)
        left.addWidget(grp_laser)

        # Conexión osciloscopio
        grp_scope = QtWidgets.QGroupBox("OSCILOSCOPIO")
        fs = QtWidgets.QFormLayout(grp_scope)
        fs.setSpacing(6)
        self.inp_ip  = QtWidgets.QLineEdit(SCOPE_IP)
        self.inp_ch  = QtWidgets.QLineEdit(SCOPE_CHANNEL)
        self.inp_ip.setFixedWidth(140)
        self.inp_ch.setFixedWidth(60)
        fs.addRow("IP:", self.inp_ip)
        fs.addRow("Canal:", self.inp_ch)
        left.addWidget(grp_scope)

        # Peak display
        grp_peak = QtWidgets.QGroupBox("PICO DETECTADO")
        vp = QtWidgets.QVBoxLayout(grp_peak)
        vp.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_peak_val = QtWidgets.QLabel("—")
        self.lbl_peak_val.setObjectName("peak_val")
        self.lbl_peak_val.setAlignment(QtCore.Qt.AlignCenter)
        lbl_peak_u = QtWidgets.QLabel("VOLTAJE PICO (mV)")
        lbl_peak_u.setObjectName("peak_lbl")
        lbl_peak_u.setAlignment(QtCore.Qt.AlignCenter)
        vp.addWidget(self.lbl_peak_val)
        vp.addWidget(lbl_peak_u)
        left.addWidget(grp_peak)

        # Estado
        self.lbl_status = QtWidgets.QLabel("Listo")
        self.lbl_status.setObjectName("status_ok")
        self.lbl_status.setAlignment(QtCore.Qt.AlignCenter)
        left.addWidget(self.lbl_status)

        # Botón principal
        self.btn_medir = QtWidgets.QPushButton("⚡  INICIAR MEDICIÓN")
        self.btn_medir.setObjectName("btn_medir")
        self.btn_medir.setFixedWidth(240)
        self.btn_medir.clicked.connect(self._start_measurement)
        left.addWidget(self.btn_medir)

        left.addStretch()

        # ── Panel derecho ──────────────────────────────────────
        right = QtWidgets.QVBoxLayout()
        right.setSpacing(8)
        body.addLayout(right, stretch=1)

        # Gráfica
        pg.setConfigOption("background", DARK_BG)
        pg.setConfigOption("foreground", TEXT_DIM)

        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setLabel("left",   "Voltaje", units="V",
                                   **{"color": TEXT_DIM, "font-size": "11px"})
        self.plot_widget.setLabel("bottom", "Tiempo",  units="s",
                                   **{"color": TEXT_DIM, "font-size": "11px"})
        self.plot_widget.showGrid(x=True, y=True, alpha=0.15)
        self.plot_widget.getAxis("left").setPen(pg.mkPen(BORDER))
        self.plot_widget.getAxis("bottom").setPen(pg.mkPen(BORDER))
        self.plot_widget.setMinimumHeight(320)

        self._curve = self.plot_widget.plot(pen=pg.mkPen(CYAN, width=1.5))
        self._peak_scatter = pg.ScatterPlotItem(
            size=12, brush=pg.mkBrush(AMBER), pen=pg.mkPen(None)
        )
        self.plot_widget.addItem(self._peak_scatter)
        right.addWidget(self.plot_widget, stretch=1)

        # Log
        log_hdr = QtWidgets.QHBoxLayout()
        log_lbl = QtWidgets.QLabel("REGISTRO")
        log_lbl.setStyleSheet(f"color:{TEXT_DIM}; font-size:11px; letter-spacing:1px;")
        self.btn_clear = QtWidgets.QPushButton("limpiar")
        self.btn_clear.setObjectName("btn_clear")
        self.btn_clear.clicked.connect(lambda: self.log_box.clear())
        log_hdr.addWidget(log_lbl)
        log_hdr.addStretch()
        log_hdr.addWidget(self.btn_clear)
        right.addLayout(log_hdr)

        self.log_box = QtWidgets.QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(160)
        right.addWidget(self.log_box)

        # CSV path
        self.lbl_csv = QtWidgets.QLabel("")
        self.lbl_csv.setStyleSheet(f"color:{TEXT_DIM}; font-size:11px;")
        right.addWidget(self.lbl_csv)

    # ── Slots ─────────────────────────────────────────────────
    def _log(self, msg: str, level: str = "info"):
        colors = {"info": TEXT, "ok": GREEN, "error": RED}
        color  = colors.get(level, TEXT)
        ts     = datetime.now().strftime("%H:%M:%S")
        self.log_box.append(
            f'<span style="color:{TEXT_DIM}">[{ts}]</span> '
            f'<span style="color:{color}">{msg}</span>'
        )
        sb = self.log_box.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _start_measurement(self):
        self.btn_medir.setEnabled(False)
        self.lbl_status.setObjectName("status_run")
        self.lbl_status.setText("⏳ Midiendo...")
        self.lbl_status.setStyleSheet(f"color:{AMBER}; font-weight:bold;")
        self.lbl_peak_val.setText("—")
        self._curve.setData([], [])
        self._peak_scatter.setData([], [])
        self.lbl_csv.setText("")

        self._thread = QtCore.QThread()
        self._worker = MeasurementWorker(
            laser_port = self.inp_com.text().strip(),
            scope_ip   = self.inp_ip.text().strip(),
            channel    = self.inp_ch.text().strip().upper(),
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self._log)
        self._worker.waveform_ready.connect(self._on_waveform)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.start()

    @QtCore.Slot(object, object, float, str)
    def _on_waveform(self, tiempo, voltaje, pico_V, csv_path):
        self._curve.setData(tiempo * 1000, voltaje * 1000)  # ms y mV
        pico_idx = int(np.argmax(np.abs(voltaje)))
        self._peak_scatter.setData(
            x=[tiempo[pico_idx] * 1000], y=[voltaje[pico_idx] * 1000]
        )
        self.lbl_peak_val.setText(f"{pico_V*1000:.2f}")
        self.lbl_csv.setText(f"💾  {csv_path}")

        # Centrar vista ±20ms alrededor del pico
        t_pico_ms = tiempo[pico_idx] * 1000
        self.plot_widget.setXRange(t_pico_ms - 20, t_pico_ms + 20)
        self.plot_widget.setLabel("left",   "Voltaje", units="mV")
        self.plot_widget.setLabel("bottom", "Tiempo",  units="ms")
        self.plot_widget.enableAutoRange(axis='y')

    @QtCore.Slot(bool)
    def _on_finished(self, success: bool):
        self.btn_medir.setEnabled(True)
        if success:
            self.lbl_status.setText("✓ Medición completada")
            self.lbl_status.setStyleSheet(f"color:{GREEN}; font-weight:bold;")
        else:
            self.lbl_status.setText("✗ Error — revisa el registro")
            self.lbl_status.setStyleSheet(f"color:{RED}; font-weight:bold;")


# ─────────────────────────────────────────────────────────────
#  ENTRADA
# ─────────────────────────────────────────────────────────────
def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
