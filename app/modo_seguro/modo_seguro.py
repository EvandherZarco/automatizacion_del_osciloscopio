"""
modo_seguro.py
Orquestador del modo seguro del láser.
Llamado por: GUI (stop de emergencia), Medición (fin de secuencia o fallo),
             Conexión y monitoreo (fallo irrecuperable de reconexión),
             timeout de inactividad en modo manual.

El osciloscopio nunca se toca — solo el láser retorna a parámetros seguros.
"""

from PySide6.QtCore import QObject, Signal

from app.laser.control_laser import LaserController

ETIQUETAS_COMANDOS: dict[str, str] = {
    "stop":          "STOP",
    "eo_delay_3800": "EO delay 3800",
    "e_off":         "E OFF",
    "burst_cont":    "Burst Continuous",
}

COMANDOS_DETIENEN_HAZ: tuple[str, ...] = ("stop", "e_off")


class ModoSeguro(QObject):

    activado = Signal()  # modo seguro iniciado
    completado = Signal(bool, list)  # (todo_ok, lista_de_fallidos)

    def __init__(self, laser: LaserController, parent=None):
        super().__init__(parent)
        self._laser = laser

    def activar(self) -> dict[str, bool]:
        """
        Envía los cuatro comandos de seguridad al láser en orden.
        Siempre intenta todos, incluso si alguno falla.
        Emite completado(True, []) en éxito o completado(False, [lista]) en fallo parcial.
        Retorna el resultado de cada comando (clave → confirmado por el láser).
        """
        self.activado.emit()

        resultados = self._laser.modo_seguro()

        fallidos = [cmd for cmd, ok in resultados.items() if not ok]
        todo_ok = len(fallidos) == 0

        self.completado.emit(todo_ok, fallidos)
        return resultados
