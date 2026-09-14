"""
dialogo_conexion.py
Diálogo modal "Conexión": permite elegir el puerto del ESP32, el puerto del
láser y la IP del osciloscopio sin editar código. Los valores se guardan en
el JSON de usuario (app.config_usuario) y quedan vigentes para la siguiente
conexión de cada dispositivo.

"Probar conexión" sondea los dispositivos con los valores que están en el
diálogo, sin guardarlos, en un hilo aparte para no congelar la ventana.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, QObject, Signal, Slot
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QComboBox, QSpinBox, QPushButton, QTextEdit, QMessageBox, QFrame,
)

from app import config_usuario
from app.conexion import prueba_conexion
from app.gui.theme import AZUL, VERDE, ROJO, AMARILLO

NOMBRES_DISPOSITIVO = {
    "TEMP_COM_PORT":  "ESP32 (temperatura)",
    "LASER_COM_PORT": "Láser",
    "OSCIL_HOST":     "Osciloscopio",
}

_ESTILO_AVISO = f"color: {AMARILLO}; font-size: 11px; background: transparent;"
_ESTILO_NOTA  = "color: #888; font-size: 11px; background: transparent;"
_ESTILO_SEGURIDAD = (
    f"QLabel {{ color: {AMARILLO}; background: #2a2000; border: 1px solid {AMARILLO};"
    " border-radius: 4px; padding: 6px 10px; font-size: 12px; }"
)

TOOLTIP_CONEXION = "Puertos COM del ESP32 y del láser, e IP del osciloscopio"
MOTIVO_LASER_RUN = "El láser está en RUN. Deténgalo antes de cambiar la conexión."
MOTIVO_SECUENCIA = "Hay una secuencia de medición en curso. Espere a que termine o deténgala."
MOTIVO_CAPTURA   = "Hay una captura en curso. Espere a que termine."


def actualizar_boton_conexion(boton, motivo: str | None, tooltip: str = TOOLTIP_CONEXION) -> None:
    """
    Habilita el botón "Conexión" o lo deshabilita mostrando el motivo como
    tooltip. motivo=None significa que se puede abrir el diálogo.
    """
    boton.setEnabled(motivo is None)
    boton.setToolTip(tooltip if motivo is None else motivo)


def texto_fallo_conexion(fallos: list[tuple[str, str]]) -> str:
    """
    Mensaje para el usuario cuando uno o más dispositivos no conectan.
    fallos: lista de (campo, puerto_o_direccion).
    """
    lineas = [
        f"•  {NOMBRES_DISPOSITIVO.get(campo, campo)}: sin respuesta en {direccion}"
        for campo, direccion in fallos
    ]
    return (
        "No fue posible conectar con:\n\n" + "\n".join(lineas) + "\n\n"
        "Revise el puerto o la dirección en Conexión (botón de la barra "
        "superior), pruebe la conexión y guarde los cambios."
    )


class _CampoIP(QWidget):
    """Cuatro casillas numéricas 0-255 separadas por puntos."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._octetos: list[QSpinBox] = []
        for i in range(4):
            sb = QSpinBox()
            sb.setRange(0, 255)
            sb.setButtonSymbols(QSpinBox.NoButtons)
            sb.setAlignment(Qt.AlignCenter)
            sb.setFixedWidth(56)
            self._octetos.append(sb)
            lay.addWidget(sb)
            if i < 3:
                punto = QLabel(".")
                punto.setStyleSheet("font-weight: bold; background: transparent;")
                lay.addWidget(punto)
        lay.addStretch()

    def texto(self) -> str:
        return ".".join(str(sb.value()) for sb in self._octetos)

    def set_texto(self, ip: str) -> bool:
        partes = ip.strip().split(".")
        try:
            valores = [int(p) for p in partes]
        except ValueError:
            valores = []
        if len(valores) != 4 or not all(0 <= v <= 255 for v in valores):
            for sb in self._octetos:
                sb.setValue(0)
            return False
        for sb, v in zip(self._octetos, valores):
            sb.setValue(v)
        return True


class _PruebaWorker(QObject):
    """Ejecuta los sondeos en un hilo secundario y reporta cada resultado."""

    resultado = Signal(str, bool, str)
    terminado = Signal()

    def __init__(self, pruebas: list[tuple[str, str]]):
        super().__init__()
        self._pruebas = pruebas

    @Slot()
    def ejecutar(self):
        for campo, valor in self._pruebas:
            if campo == "TEMP_COM_PORT":
                ok, msg = prueba_conexion.probar_esp32(valor)
            elif campo == "LASER_COM_PORT":
                ok, msg = prueba_conexion.probar_laser(valor)
            else:
                ok, msg = prueba_conexion.probar_osciloscopio(valor)
            self.resultado.emit(campo, ok, msg)
        self.terminado.emit()


class DialogoConexion(QDialog):
    """
    activos: direcciones que la aplicación tiene abiertas en este momento,
    por campo (None si ese dispositivo no está conectado). Se usan para no
    sondear un puerto que la propia aplicación ya ocupa.
    """

    def __init__(self, parent=None, activos: dict[str, str | None] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Conexión de dispositivos")
        self.setModal(True)
        self.setMinimumWidth(560)

        self._activos = activos or {}
        self._valores_guardados: dict[str, str] | None = None
        self._hilo_prueba: QThread | None = None
        self._worker_prueba: _PruebaWorker | None = None

        self._construir_ui()
        self._cargar_valores(config_usuario.cargar())

    # ── Construcción ─────────────────────────────────────────────────────────

    def _construir_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        titulo = QLabel("Puertos y dirección de los dispositivos")
        titulo.setStyleSheet(f"color: {AZUL}; font-size: 14px; font-weight: bold; background: transparent;")
        root.addWidget(titulo)

        nota = QLabel(
            "Los cambios se guardan en la carpeta del usuario y se aplican en la "
            "siguiente conexión de cada dispositivo."
        )
        nota.setWordWrap(True)
        nota.setStyleSheet(_ESTILO_NOTA)
        root.addWidget(nota)

        seguridad = QLabel(
            "⚠  El láser puede estar emitiendo por su control CAN externo aunque "
            "esta aplicación no esté conectada a él. Antes de probar la conexión, "
            "verifique que el láser esté detenido y las protecciones colocadas."
        )
        seguridad.setWordWrap(True)
        seguridad.setStyleSheet(_ESTILO_SEGURIDAD)
        root.addWidget(seguridad)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)

        self._cmb_esp32 = QComboBox()
        self._cmb_laser = QComboBox()
        self._ip_oscil = _CampoIP()

        grid.addWidget(QLabel("ESP32 (temperatura):"), 0, 0, Qt.AlignRight)
        grid.addWidget(self._cmb_esp32, 0, 1)
        grid.addWidget(QLabel("Láser:"), 1, 0, Qt.AlignRight)
        grid.addWidget(self._cmb_laser, 1, 1)
        grid.addWidget(QLabel("IP del osciloscopio:"), 2, 0, Qt.AlignRight)
        grid.addWidget(self._ip_oscil, 2, 1)
        root.addLayout(grid)

        self._lbl_aviso = QLabel("")
        self._lbl_aviso.setWordWrap(True)
        self._lbl_aviso.setStyleSheet(_ESTILO_AVISO)
        self._lbl_aviso.setVisible(False)
        root.addWidget(self._lbl_aviso)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #2e2e2e; background: #2e2e2e; max-height: 1px;")
        root.addWidget(sep)

        self._txt_resultado = QTextEdit()
        self._txt_resultado.setReadOnly(True)
        self._txt_resultado.setFixedHeight(96)
        self._txt_resultado.setPlaceholderText(
            "Pulse «Probar conexión» para verificar los valores actuales del diálogo."
        )
        self._txt_resultado.setStyleSheet(
            "QTextEdit { background: #111; border: 1px solid #2e2e2e; border-radius: 4px;"
            " font-size: 12px; padding: 4px; }"
        )
        root.addWidget(self._txt_resultado)

        botones = QHBoxLayout()
        botones.setSpacing(8)
        self._btn_actualizar = QPushButton("↻ Actualizar lista")
        self._btn_probar = QPushButton("Probar conexión")
        self._btn_guardar = QPushButton("Guardar")
        self._btn_cancelar = QPushButton("Cancelar")
        self._btn_guardar.setProperty("activo", True)
        self._btn_guardar.setDefault(True)
        botones.addWidget(self._btn_actualizar)
        botones.addWidget(self._btn_probar)
        botones.addStretch()
        botones.addWidget(self._btn_guardar)
        botones.addWidget(self._btn_cancelar)
        root.addLayout(botones)

        self._btn_actualizar.clicked.connect(self._on_actualizar)
        self._btn_probar.clicked.connect(self._on_probar)
        self._btn_guardar.clicked.connect(self._on_guardar)
        self._btn_cancelar.clicked.connect(self.reject)

    # ── Carga de valores ─────────────────────────────────────────────────────

    def _cargar_valores(self, valores: dict[str, str]):
        avisos = []
        puertos = config_usuario.enumerar_puertos()
        avisos += self._poblar_combo(self._cmb_esp32, puertos, valores["TEMP_COM_PORT"], "ESP32")
        avisos += self._poblar_combo(self._cmb_laser, puertos, valores["LASER_COM_PORT"], "láser")
        if not self._ip_oscil.set_texto(valores["OSCIL_HOST"]):
            avisos.append(
                f"La dirección guardada «{valores['OSCIL_HOST']}» no es una IPv4 válida; "
                "capture la dirección correcta."
            )
        if not puertos:
            avisos.append("No se detectó ningún puerto serie en este equipo.")
        self._lbl_aviso.setText("\n".join(avisos))
        self._lbl_aviso.setVisible(bool(avisos))

    @staticmethod
    def _poblar_combo(combo: QComboBox, puertos, actual: str, nombre: str) -> list[str]:
        combo.clear()
        for p in puertos:
            etiqueta = f"{p.nombre} — {p.descripcion}" if p.descripcion else p.nombre
            combo.addItem(etiqueta, p.nombre)
        idx = combo.findData(actual)
        avisos = []
        if idx < 0:
            combo.addItem(f"{actual} — (no detectado ahora)", actual)
            idx = combo.count() - 1
            avisos.append(
                f"El puerto {actual} configurado para el {nombre} no aparece entre los "
                "detectados. Se conserva hasta que elija otro."
            )
        combo.setCurrentIndex(idx)
        return avisos

    def _valores_del_dialogo(self) -> dict[str, str]:
        return {
            "TEMP_COM_PORT":  self._cmb_esp32.currentData() or "",
            "LASER_COM_PORT": self._cmb_laser.currentData() or "",
            "OSCIL_HOST":     self._ip_oscil.texto(),
        }

    def valores(self) -> dict[str, str] | None:
        """Valores guardados al aceptar, o None si se canceló."""
        return self._valores_guardados

    # ── Acciones ─────────────────────────────────────────────────────────────

    @Slot()
    def _on_actualizar(self):
        self._cargar_valores(self._valores_del_dialogo())
        self._txt_resultado.clear()

    @Slot()
    def _on_probar(self):
        if self._hilo_prueba is not None:
            return
        valores = self._valores_del_dialogo()
        self._txt_resultado.clear()

        pruebas: list[tuple[str, str]] = []
        for campo in ("TEMP_COM_PORT", "LASER_COM_PORT", "OSCIL_HOST"):
            valor = valores[campo]
            nombre = NOMBRES_DISPOSITIVO[campo]
            activo = self._activos.get(campo)
            if not valor:
                self._agregar_resultado(False, f"{nombre}: no hay puerto seleccionado.")
                continue
            if activo and activo == valor:
                self._agregar_resultado(True, f"{nombre}: ya está conectado en {valor}.")
                continue
            if activo and campo == "LASER_COM_PORT":
                self._agregar_resultado(
                    False,
                    f"{nombre}: la aplicación lo tiene conectado en {activo}; "
                    f"desconéctelo antes de probar {valor}.",
                )
                continue
            pruebas.append((campo, valor))

        if not pruebas:
            return

        self._set_ocupado(True)
        self._txt_resultado.append('<span style="color:#888;">Probando…</span>')

        worker = _PruebaWorker(pruebas)
        hilo = QThread(self)
        worker.moveToThread(hilo)
        hilo.started.connect(worker.ejecutar)
        worker.resultado.connect(self._on_resultado_prueba)
        worker.terminado.connect(self._on_prueba_terminada)
        worker.terminado.connect(hilo.quit)
        hilo.finished.connect(worker.deleteLater)
        hilo.finished.connect(hilo.deleteLater)
        self._hilo_prueba = hilo
        self._worker_prueba = worker
        hilo.start()

    @Slot(str, bool, str)
    def _on_resultado_prueba(self, _campo: str, ok: bool, mensaje: str):
        self._agregar_resultado(ok, mensaje)

    @Slot()
    def _on_prueba_terminada(self):
        self._hilo_prueba = None
        self._worker_prueba = None
        self._set_ocupado(False)

    def _agregar_resultado(self, ok: bool, mensaje: str):
        color = VERDE if ok else ROJO
        marca = "✔" if ok else "✖"
        self._txt_resultado.append(f'<span style="color:{color};">{marca}  {mensaje}</span>')

    def _set_ocupado(self, ocupado: bool):
        for b in (self._btn_actualizar, self._btn_probar, self._btn_guardar, self._btn_cancelar):
            b.setEnabled(not ocupado)
        self._cmb_esp32.setEnabled(not ocupado)
        self._cmb_laser.setEnabled(not ocupado)
        self._ip_oscil.setEnabled(not ocupado)

    @Slot()
    def _on_guardar(self):
        valores = self._valores_del_dialogo()
        faltantes = [NOMBRES_DISPOSITIVO[c] for c, v in valores.items() if not v]
        if faltantes:
            QMessageBox.warning(
                self, "Faltan datos",
                "Seleccione un puerto para: " + ", ".join(faltantes) + ".",
            )
            return
        try:
            config_usuario.guardar(valores)
        except OSError:
            QMessageBox.critical(
                self, "No se pudo guardar",
                "No fue posible escribir la configuración en:\n"
                f"{config_usuario.ruta_config()}\n\n"
                "Verifique que la carpeta exista y tenga permisos de escritura.",
            )
            return
        config_usuario.aplicar(valores)
        self._valores_guardados = valores
        self.accept()

    def reject(self):
        if self._hilo_prueba is not None:
            return
        super().reject()

    def closeEvent(self, event):
        if self._hilo_prueba is not None:
            event.ignore()
            return
        super().closeEvent(event)
