"""
control_osciloscopio.py
Control del osciloscopio Tektronix TDS5052B via VXI-11 sobre Ethernet.
Protocolo verificado en hardware con python-vxi11.
Prerequisito: app interna del osciloscopio corriendo en Windows XP.

Modos de operación:
  Manual           — NUMAVG=1, captura única sin promediado.
  Modo tiempo      — el scope promedia NUMAVG_TIEMPO disparos internamente
                     y para solo; Python hace polling de ACQ:STATE? hasta 0.
  Modo temperatura — Python controla la ventana con acq_run() / acq_stop_and_capture().
                     NUMAVG se pone en 10000 para que el scope nunca pare solo.

Toda captura sigue ACQ:NUMACQ? con TestigoNumacq durante su ventana de
adquisición: si el contador no subió, el registro leído es el anterior y la
captura se marca con error_flag. Medido en el TDS5054B: ACQ:STATE RUN no
reinicia el contador; un cambio de escala sí, con un retraso de hasta ~1 s,
de modo que el reinicio se reconoce como una caída entre lecturas. En la
captura manual y en modo tiempo, con ACQ:MODE AVERAGE se exige además que las
adquisiciones desde el último reinicio lleguen a NUMAVG. Cada captura lleva
en sus metadatos ACQ:MODE?, ACQ:NUMAVG? y ACQ:NUMACQ? leído justo antes de
CURVE?. Fuera de la ventana de modo temperatura el osciloscopio se deja
siempre en RUN, verificado con ACQ:STATE?, y al cerrar una secuencia
automática recupera el modo y el promediado que tenía antes de empezarla.
"""

from __future__ import annotations

import logging
import math
import time
import numpy as np
import vxi11
from dataclasses import dataclass
from PySide6.QtCore import QObject, Signal, QMutex, QMutexLocker

from app import config_usuario

logger = logging.getLogger(__name__)

TIMEOUT_S = 30.0
MAX_REINTENTOS = 3
POLL_INTERVAL_S = 0.5
POLL_TIMEOUT_S = 90.0

ESPERA_ADQUISICION_S = 3.0
POLL_ADQUISICION_S = 0.05
RETRASO_REINICIO_S = 1.0

SIN_ADQUISICION_NUEVA = "osciloscopio sin adquisición nueva"
ESCALA_CAMBIADA = "escala cambiada durante la captura sin reinicio visible del promedio"

NUMAVG_TIEMPO = 100
NUMAVG_TEMPERATURA = 10000

FREC_DISPARO_HZ = 10.0
TRANSFER_BYTES_POR_S = 250_000.0
OVERHEAD_TRANSFERENCIA_S = 1.0

_CANALES_VALIDOS = ("CH1", "CH2")
_CANAL_DEFAULT = "CH1"


def _es_average(modo: str) -> bool:
    return modo.strip().upper().startswith("AVE")


class TestigoNumacq:
    """
    Sigue ACQ:NUMACQ? a lo largo de una captura.

    El contador no se reinicia con ACQ:STATE RUN; un cambio de escala lo
    reinicia, pero con retraso, así que justo después del cambio todavía
    muestra el valor viejo y el reinicio solo se ve como una caída entre dos
    lecturas. La captura es fresca si el contador subió respecto a la primera
    lectura sin caer, o si cayó y después volvió a subir. Tras un reinicio el
    contador parte de cero: su último valor son las adquisiciones acumuladas
    desde el reinicio más reciente.
    """

    def __init__(self, inicial: int):
        self.inicial = inicial
        self.ultimo = inicial
        self.tras_reinicio: int | None = None

    def registrar(self, n: int) -> bool:
        """Anota una lectura; devuelve True si revela un reinicio."""
        cayo = n < self.ultimo
        if cayo:
            self.tras_reinicio = n
        self.ultimo = n
        return cayo

    @property
    def reiniciado(self) -> bool:
        return self.tras_reinicio is not None

    @property
    def fresca(self) -> bool:
        base = self.inicial if self.tras_reinicio is None else self.tras_reinicio
        return self.ultimo > base

    @property
    def desde_reinicio(self) -> int:
        return self.ultimo

    def completa(self, requeridas: int) -> bool:
        return self.fresca and self.desde_reinicio >= requeridas

    def describir(self) -> str:
        if self.tras_reinicio is None:
            return f"ACQ:NUMACQ? {self.inicial} → {self.ultimo}"
        return (
            f"ACQ:NUMACQ? {self.inicial} → reinicio a {self.tras_reinicio} → {self.ultimo}"
        )


@dataclass
class CapturaOscil:
    raw_data: np.ndarray
    wfmpre: dict
    voltaje: np.ndarray
    tiempo: np.ndarray
    error_flag: int = 0
    error_desc: str = ""

    def marcar(self, desc: str) -> None:
        self.error_flag = 1
        self.error_desc = f"{self.error_desc}; {desc}" if self.error_desc else desc


class OsciloscopioController(QObject):

    led_verde = Signal()
    led_rojo = Signal()
    led_amarillo = Signal()
    error = Signal(str)
    cmd_ok = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._inst: vxi11.Instrument | None = None
        self._mutex = QMutex()
        self._connected = False
        self._host: str = config_usuario.obtener("OSCIL_HOST")
        self._idn: str = ""
        self._modelo: str = ""
        self._canal: str = _CANAL_DEFAULT
        self._nr_pt: int = 0
        self._cancelar_espera = False
        self._numacq_inicio: int | None = None
        self._adquisicion_previa: tuple[str, int] | None = None

    @property
    def conectado(self) -> bool:
        return self._connected

    @property
    def canal(self) -> str:
        return self._canal

    @property
    def host(self) -> str:
        """Dirección IP usada en la conexión más reciente."""
        return self._host

    @property
    def idn(self) -> str:
        """Cadena *IDN? completa del instrumento conectado."""
        return self._idn

    @property
    def modelo(self) -> str:
        """Modelo extraído del *IDN?, por ejemplo 'TDS5054B'."""
        return self._modelo

    # ── Conexión ──────────────────────────────────────────────────────────────

    def conectar(self) -> bool:
        with QMutexLocker(self._mutex):
            self._host = config_usuario.obtener("OSCIL_HOST")
            try:
                inst = vxi11.Instrument(self._host)
                inst.timeout = TIMEOUT_S
            except Exception as exc:
                logger.warning("No se pudo crear el enlace VXI-11 con %s: %s", self._host, exc)
                self._emit_conn_error(
                    f"Osciloscopio: no se pudo abrir la conexión con {self._host}. "
                    "Revise la dirección IP en Conexión."
                )
                return False

            idn = self._ping(inst)
            if idn is None:
                self._emit_conn_error(
                    f"Osciloscopio: sin respuesta en {self._host}. Verifique que la app "
                    "TekScope esté corriendo, el cable Ethernet y la IP en Conexión."
                )
                return False

            try:
                modelo = idn.split(",")[1].strip()
            except IndexError:
                self._emit_conn_error(f"IDN con formato inesperado: {idn.strip()}")
                return False

            if not modelo.startswith("TDS"):
                self._emit_conn_error(f"Instrumento desconocido: {idn.strip()}")
                return False

            nr_pt = self._configurar_base(inst)
            if nr_pt is None:
                self._emit_conn_error(
                    "Error en la configuración base del osciloscopio."
                )
                return False

            self._inst = inst
            self._idn = idn.strip()
            self._modelo = modelo
            self._nr_pt = nr_pt
            self._connected = True
            self.led_verde.emit()
            self.cmd_ok.emit(
                f"Osciloscopio conectado — {modelo} ({self._host}). "
                f"Puntos de waveform: {nr_pt}."
            )
            return True

    def desconectar(self) -> None:
        with QMutexLocker(self._mutex):
            if self._inst:
                try:
                    self._inst.close()
                except Exception:
                    pass
                self._inst = None
            self._connected = False
            self.led_rojo.emit()

    def ping(self) -> bool:
        with QMutexLocker(self._mutex):
            if not self._inst:
                return False
            return self._ping(self._inst) is not None

    def cancelar_espera(self) -> None:
        self._cancelar_espera = True

    def leer_escala_actual(self) -> dict | None:
        """
        Lee del hardware los parámetros de escala actuales.
        Retorna dict con claves: vdiv_v, tdiv_s, coupling, trigger_v, acq_mode, numavg.
        Retorna None si el osciloscopio no responde.
        """
        with QMutexLocker(self._mutex):
            if not self._inst:
                return None
            try:
                vdiv = float(self._inst.ask(f"{self._canal}:SCALE?").strip())
                tdiv = float(self._inst.ask("HORizontal:SCAle?").strip())
                coup = self._inst.ask(f"{self._canal}:COUPling?").strip().upper()
                trig = float(self._inst.ask("TRIGger:MAIn:LEVel?").strip())
                mode = self._inst.ask("ACQ:MODE?").strip().upper()
                numavg = int(self._inst.ask("ACQ:NUMAVG?").strip())
                return {
                    "vdiv_v": vdiv,
                    "tdiv_s": tdiv,
                    "coupling": coup,
                    "trigger_v": trig,
                    "acq_mode": mode,
                    "numavg": numavg,
                }
            except Exception as exc:
                self.error.emit(f"Error al leer escala actual: {exc}")
                return None

    def set_acq_mode(self, modo: str) -> bool:
        """
        Establece el modo de adquisición.
        modo: 'SAMPLE' | 'AVERAGE'
        """
        modo = modo.upper()
        if modo not in ("SAMPLE", "AVERAGE"):
            self.error.emit(f"Modo de adquisición inválido: {modo}")
            return False
        with QMutexLocker(self._mutex):
            if not self._inst:
                return False
            try:
                self._inst.write(f"ACQ:MODE {modo}")
                self.cmd_ok.emit(f"Osciloscopio: adquisición → {modo}")
                return True
            except Exception as exc:
                self.error.emit(f"Error al configurar modo ACQ: {exc}")
                return False

    def set_numavg(self, n: int) -> bool:
        """Establece el número de promedios. Solo tiene efecto si ACQ:MODE = AVERAGE."""
        with QMutexLocker(self._mutex):
            if not self._inst:
                return False
            return self._set_numavg(self._inst, n)

    # ── Canal de señal ────────────────────────────────────────────────────────

    def set_canal(self, canal: str) -> bool:
        """
        Cambia el canal activo (CH1 o CH2) y lo aplica al osciloscopio.
        Solo tiene efecto cuando no hay una adquisición en curso.
        """
        canal = canal.upper()
        if canal not in _CANALES_VALIDOS:
            self.error.emit(f"Canal inválido: {canal}. Opciones: {_CANALES_VALIDOS}")
            return False

        with QMutexLocker(self._mutex):
            if not self._inst:
                self._canal = canal
                return True
            try:
                self._inst.write(f"DATA:SOURCE {canal}")
                self._canal = canal
                self.cmd_ok.emit(f"Canal activo: {canal}")
                return True
            except Exception as exc:
                self.error.emit(f"Error al cambiar canal a {canal}: {exc}")
                return False

    # ── Configuración de modo ─────────────────────────────────────────────────

    def configurar_modo_tiempo(self, numavg: int = NUMAVG_TIEMPO) -> bool:
        with QMutexLocker(self._mutex):
            if not self._inst:
                return False
            try:
                self._recordar_adquisicion(self._inst)
                self._inst.write("ACQ:MODE AVERAGE")
                self._inst.write("ACQ:STOPAFTER SEQUENCE")
            except Exception as exc:
                self._emit_conn_error(f"Error al configurar modo tiempo: {exc}")
                return False
            return self._set_numavg(self._inst, numavg)

    def configurar_modo_temperatura(self) -> bool:
        with QMutexLocker(self._mutex):
            if not self._inst:
                return False
            try:
                self._recordar_adquisicion(self._inst)
                self._inst.write("ACQ:STATE STOP")
                self._inst.write("ACQ:MODE AVERAGE")
                self._inst.write("ACQ:STOPAFTER SEQUENCE")
            except Exception as exc:
                self._emit_conn_error(f"Error al configurar modo temperatura: {exc}")
                return False
            return self._set_numavg(self._inst, NUMAVG_TEMPERATURA)

    # ── Lectura directa de pantalla ───────────────────────────────────────────

    def leer_pantalla(self) -> CapturaOscil | None:
        """
        Captura rápida que respeta la configuración actual del panel frontal.
        Espera a que el osciloscopio complete al menos una adquisición nueva
        y, en modo AVERAGE, el promedio completo; lo detiene para leer un
        registro estable y lo devuelve a RUN.
        """
        with QMutexLocker(self._mutex):
            if not self._inst:
                return None
            inst = self._inst
            try:
                return self._capturar_en_marcha(inst)
            finally:
                self._reanudar_freerun(inst)

    # ── Captura modo manual ───────────────────────────────────────────────────

    def capturar(self) -> CapturaOscil | None:
        """
        Captura única para modo manual de la GUI (NUMAVG=1, sin promediado).
        """
        with QMutexLocker(self._mutex):
            if not self._inst:
                return None
            inst = self._inst
            if not self._set_numavg(inst, 1):
                return None
            try:
                inst.write("ACQ:MODE AVERAGE")
            except Exception as exc:
                self._emit_conn_error(f"Error al iniciar adquisición manual: {exc}")
                return None
            try:
                return self._capturar_en_marcha(inst)
            finally:
                self._reanudar_freerun(inst)

    # ── Captura modo tiempo ───────────────────────────────────────────────────

    def estimar_tiempo_captura(self, numavg: int = NUMAVG_TIEMPO) -> float:
        """
        Tiempo aproximado de una captura en modo tiempo, en segundos.
        Suma el promediado (numavg disparos a la frecuencia del laser,
        cuantizado por el periodo de polling) y la transferencia de la
        forma de onda por VXI-11.
        """
        n = max(1, int(numavg))
        t_promedio = n / FREC_DISPARO_HZ
        t_promedio = math.ceil(t_promedio / POLL_INTERVAL_S) * POLL_INTERVAL_S
        puntos = self._nr_pt if self._nr_pt > 0 else 0
        t_transfer = (puntos * 2) / TRANSFER_BYTES_POR_S
        return t_promedio + t_transfer + OVERHEAD_TRANSFERENCIA_S

    def capturar_modo_tiempo(self) -> CapturaOscil | None:
        """
        Captura con promediado para modo tiempo. Requiere que NUMAVG < NUMAVG_TEMPERATURA;
        si el scope está configurado para modo temperatura este método no debe llamarse.
        """
        with QMutexLocker(self._mutex):
            if not self._inst:
                return None

            numavg_actual = self._leer_numavg_actual(self._inst)
            if numavg_actual is not None and numavg_actual >= NUMAVG_TEMPERATURA:
                self.error.emit(
                    f"capturar_modo_tiempo llamado con NUMAVG={numavg_actual}. "
                    "Usa acq_run / acq_stop_and_capture para modo temperatura."
                )
                return None

            inst = self._inst
            try:
                try:
                    inst.write("ACQ:STATE STOP")
                    inst.write("ACQ:STOPAFTER SEQUENCE")
                    inst.write("ACQ:STATE RUN")
                    testigo = TestigoNumacq(self._leer_numacq(inst))
                except Exception as exc:
                    self._emit_conn_error(f"Error al iniciar adquisición: {exc}")
                    return None

                terminado = self._esperar_fin(inst)
                if not self._connected:
                    return None

                captura = self._leer_waveform(inst)
                if captura is None:
                    return None
                if not terminado:
                    self.error.emit(
                        f"Timeout: el osciloscopio no terminó el promedio en {POLL_TIMEOUT_S:.0f} s."
                    )
                    self.led_amarillo.emit()
                    captura.marcar("el osciloscopio no terminó el promedio")
                self._verificar_adquisicion(captura, testigo, exigir_promedio=True)
                return captura
            finally:
                self._reanudar_freerun(inst)

    # ── Captura modo temperatura ──────────────────────────────────────────────

    def acq_run(self) -> bool:
        with QMutexLocker(self._mutex):
            if not self._inst:
                return False
            try:
                self._inst.write("ACQ:STATE RUN")
                self._numacq_inicio = self._leer_numacq(self._inst)
                return True
            except Exception as exc:
                self._numacq_inicio = None
                self._emit_conn_error(f"Error al iniciar ACQ:STATE RUN: {exc}")
                return False

    def acq_stop_and_capture(self) -> CapturaOscil | None:
        with QMutexLocker(self._mutex):
            if not self._inst:
                return None
            n_inicio, self._numacq_inicio = self._numacq_inicio, None
            try:
                self._inst.write("ACQ:STATE STOP")
                self._inst.ask("*OPC?")
            except Exception as exc:
                self._emit_conn_error(f"Error al detener adquisición: {exc}")
                return None
            captura = self._leer_waveform(self._inst)
            if captura is not None:
                testigo = TestigoNumacq(n_inicio) if n_inicio is not None else None
                self._verificar_adquisicion(captura, testigo, exigir_promedio=False)
            return captura

    def reanudar_adquisicion(self) -> bool:
        """
        Al cerrar una secuencia, devuelve el modo y el promediado que el
        osciloscopio tenía antes de configurarla y lo deja en RUN libre.
        """
        with QMutexLocker(self._mutex):
            if not self._inst:
                return False
            previa, self._adquisicion_previa = self._adquisicion_previa, None
            if previa is not None:
                modo, numavg = previa
                try:
                    self._inst.write("ACQ:STATE STOP")
                    self._inst.write(f"ACQ:MODE {modo}")
                    self._inst.write(f"ACQ:NUMAVG {numavg}")
                except Exception as exc:
                    self._emit_capture_warning(
                        f"No se pudo restaurar ACQ:MODE {modo} / NUMAVG {numavg}: {exc}"
                    )
            return self._reanudar_freerun(self._inst)

    # ── Helpers internos ──────────────────────────────────────────────────────

    def _ping(self, inst: vxi11.Instrument) -> str | None:
        try:
            return inst.ask("*IDN?")
        except Exception:
            return None

    def _configurar_base(self, inst: vxi11.Instrument) -> int | None:
        """
        Envía configuración base de transferencia de datos.
        Solo configura el formato de transferencia, no toca parámetros
        de adquisición (modo, promediado, stopafter) para no alterar
        lo que el usuario configuró en el panel frontal.
        """
        cmds_pre = [
            f"DATA:SOURCE {self._canal}",
            "DATA:ENCDG RIBINARY",
            "DATA:WIDTH 2",
            "DATA:START 1",
            "DATA:STOP 1000000",
        ]
        try:
            for cmd in cmds_pre:
                inst.write(cmd)
            nr_pt = int(inst.ask("WFMPRE:NR_PT?").strip())
            return nr_pt
        except Exception as exc:
            self._emit_conn_error(f"Error en configuración base: {exc}")
            return None

    def _set_numavg(self, inst: vxi11.Instrument, n: int) -> bool:
        try:
            inst.write(f"ACQ:NUMAVG {n}")
            return True
        except Exception as exc:
            self._emit_conn_error(f"Error al configurar ACQ:NUMAVG {n}: {exc}")
            return False

    def _leer_numavg_actual(self, inst: vxi11.Instrument) -> int | None:
        try:
            return int(inst.ask("ACQ:NUMAVG?").strip())
        except Exception:
            return None

    def _leer_numacq(self, inst: vxi11.Instrument) -> int:
        return int(inst.ask("ACQ:NUMACQ?").strip())

    def _capturar_en_marcha(self, inst: vxi11.Instrument) -> CapturaOscil | None:
        """
        Con el osciloscopio en RUN libre, espera a que la captura sea fresca
        y, en modo AVERAGE, a que las adquisiciones desde el último reinicio
        lleguen a NUMAVG; lo detiene y lee el registro.

        Como el reinicio por cambio de escala tarda en verse, la captura no
        se acepta hasta que el contador lleve RETRASO_REINICIO_S sin caer:
        así una perilla movida justo antes de capturar alcanza a mostrarse.
        Un cambio más tardío, aún invisible en el contador, se detecta
        comparando las escalas con las vigentes al último reinicio visto.
        Cada reinicio visto vuelve a dar el plazo completo. Si el plazo
        vence, la captura se devuelve igualmente, marcada.
        """
        self._cancelar_espera = False
        try:
            inst.write("ACQ:STOPAFTER RUNSTOP")
            if inst.ask("ACQ:STATE?").strip() == "0":
                inst.write("ACQ:STATE RUN")
            testigo = TestigoNumacq(self._leer_numacq(inst))
            escalas = self._leer_escalas(inst)
            requeridas = self._adquisiciones_requeridas(inst)
            espera = ESPERA_ADQUISICION_S + RETRASO_REINICIO_S + requeridas / FREC_DISPARO_HZ
            estable_desde = time.monotonic()
            tope = estable_desde + POLL_TIMEOUT_S
            limite = min(estable_desde + espera, tope)
            while time.monotonic() < limite and not self._cancelar_espera:
                if testigo.registrar(self._leer_numacq(inst)):
                    escalas = self._leer_escalas(inst)
                    estable_desde = time.monotonic()
                    limite = min(estable_desde + espera, tope)
                estable = time.monotonic() - estable_desde >= RETRASO_REINICIO_S
                if estable and testigo.completa(requeridas):
                    break
                time.sleep(POLL_ADQUISICION_S)
            inst.write("ACQ:STATE STOP")
            inst.ask("*OPC?")
        except Exception as exc:
            self._emit_conn_error(f"Error en lectura de pantalla: {exc}")
            return None

        captura = self._leer_waveform(inst)
        if captura is not None:
            self._verificar_adquisicion(captura, testigo, exigir_promedio=True)
            leidas = (captura.wfmpre["CH_SCALE"], captura.wfmpre["HOR_SCALE"])
            if leidas != escalas:
                captura.marcar(ESCALA_CAMBIADA)
                self._emit_capture_warning(
                    f"La escala cambió durante la captura (CH/HOR {escalas} → {leidas}) "
                    "sin que ACQ:NUMACQ? mostrara aún el reinicio. "
                    "La captura se marca con error_flag."
                )
        return captura

    def _leer_escalas(self, inst: vxi11.Instrument) -> tuple[float, float]:
        return (
            float(inst.ask(f"{self._canal}:SCALE?").strip()),
            float(inst.ask("HORizontal:SCAle?").strip()),
        )

    def _adquisiciones_requeridas(self, inst: vxi11.Instrument) -> int:
        """NUMAVG en modo AVERAGE; una sola adquisición en cualquier otro modo."""
        if _es_average(inst.ask("ACQ:MODE?")):
            return max(1, int(inst.ask("ACQ:NUMAVG?").strip()))
        return 1

    def _verificar_adquisicion(
        self, captura: CapturaOscil, testigo: TestigoNumacq | None, exigir_promedio: bool
    ) -> None:
        """
        Agrega al testigo el ACQ:NUMACQ? leído justo antes de CURVE?, que
        también delata un reinicio aparecido tras detener la adquisición.
        Con exigir_promedio, en modo AVERAGE marca además el promedio que no
        llegó a NUMAVG desde el último reinicio.
        """
        w = captura.wfmpre
        n_fin = w["adquisiciones_promediadas"]
        if testigo is None:
            testigo = TestigoNumacq(n_fin)
        else:
            testigo.registrar(n_fin)
        if not testigo.fresca:
            captura.marcar(SIN_ADQUISICION_NUEVA)
            self._emit_capture_warning(
                f"Osciloscopio sin adquisición nueva ({testigo.describir()}): "
                "el registro leído no es posterior al inicio de la captura "
                "y se marca con error_flag."
            )
        if exigir_promedio and _es_average(w["acq_mode"]) and testigo.desde_reinicio < w["numavg"]:
            k = testigo.desde_reinicio
            captura.marcar(f"promedio incompleto ({k} de {w['numavg']})")
            self._emit_capture_warning(
                f"Promedio incompleto: {k} de {w['numavg']} adquisiciones desde el "
                "último reinicio. La captura se marca con error_flag."
            )

    def _recordar_adquisicion(self, inst: vxi11.Instrument) -> None:
        """Guarda ACQ:MODE y NUMAVG previos a una secuencia, una sola vez por secuencia."""
        if self._adquisicion_previa is None:
            modo = inst.ask("ACQ:MODE?").strip().upper()
            numavg = int(inst.ask("ACQ:NUMAVG?").strip())
            self._adquisicion_previa = (modo, numavg)

    def _esperar_fin(self, inst: vxi11.Instrument) -> bool:
        self._cancelar_espera = False
        deadline = time.monotonic() + POLL_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._cancelar_espera:
                return False
            try:
                state = inst.ask("ACQ:STATE?").strip()
                if state == "0":
                    return True
            except Exception as exc:
                self._emit_conn_error(f"Error al consultar ACQ:STATE?: {exc}")
                return False
            time.sleep(POLL_INTERVAL_S)
        return False

    def _leer_waveform(self, inst: vxi11.Instrument) -> CapturaOscil | None:
        wfmpre = self._leer_wfmpre(inst)
        if wfmpre is None:
            return None
        raw = self._leer_curve(inst)
        if raw is None:
            return None
        voltaje, tiempo = self._convertir(raw, wfmpre)
        captura = CapturaOscil(raw_data=raw, wfmpre=wfmpre, voltaje=voltaje, tiempo=tiempo)
        if len(raw) != wfmpre["NR_PT"]:
            captura.marcar(
                f"curva de {len(raw)} puntos y NR_PT={wfmpre['NR_PT']}: "
                "metadatos no corresponden al registro"
            )
            self._emit_capture_warning(
                f"La curva leída tiene {len(raw)} puntos pero WFMPRE:NR_PT? "
                f"indica {wfmpre['NR_PT']}."
            )
        return captura

    def _leer_wfmpre(self, inst: vxi11.Instrument) -> dict | None:
        params = ["XINCR", "XZERO", "PT_OFF", "YMULT", "YOFF", "YZERO", "NR_PT"]
        for intento in range(MAX_REINTENTOS):
            try:
                wfmpre = {}
                for p in params:
                    raw = inst.ask(f"WFMPRE:{p}?").strip()
                    wfmpre[p] = int(raw) if p in ("PT_OFF", "NR_PT") else float(raw)
                wfmpre["CH_SCALE"] = float(inst.ask(f"{self._canal}:SCALE?").strip())
                wfmpre["HOR_SCALE"] = float(inst.ask("HORizontal:SCAle?").strip())
                wfmpre["acq_mode"] = inst.ask("ACQ:MODE?").strip().upper()
                wfmpre["numavg"] = int(inst.ask("ACQ:NUMAVG?").strip())
                wfmpre["adquisiciones_promediadas"] = self._leer_numacq(inst)
                return wfmpre
            except Exception as exc:
                if intento == MAX_REINTENTOS - 1:
                    self._emit_capture_warning(
                        f"No se pudieron leer parámetros WFMPRE: {exc}"
                    )
        return None

    def _leer_curve(self, inst: vxi11.Instrument) -> np.ndarray | None:
        for intento in range(MAX_REINTENTOS):
            try:
                inst.write("CURVE?")
                raw_bytes = inst.read_raw()
                resultado = self._parsear_curve(raw_bytes)
                if resultado is not None:
                    return resultado
            except Exception as exc:
                if intento == MAX_REINTENTOS - 1:
                    self._emit_capture_warning(f"Error al leer CURVE: {exc}")
        return None

    def _parsear_curve(self, raw_bytes: bytes) -> np.ndarray | None:
        if len(raw_bytes) < 2 or chr(raw_bytes[0]) != "#":
            self._emit_capture_warning("Respuesta CURVE con formato inesperado.")
            return None

        n_digits = int(chr(raw_bytes[1]))
        data_start = 2 + n_digits
        data = raw_bytes[data_start:]

        if data and data[-1] == ord("\n"):
            data = data[:-1]

        arr = np.frombuffer(data, dtype=np.dtype(">i2")).copy()

        if len(arr) == 0:
            self._emit_capture_warning("CURVE devolvió un array vacío.")
            return None

        return arr

    def _convertir(
        self, raw: np.ndarray, wfmpre: dict
    ) -> tuple[np.ndarray, np.ndarray]:
        n = len(raw)
        voltaje = (raw.astype(np.float64) - wfmpre["YOFF"]) * wfmpre["YMULT"] + wfmpre["YZERO"]
        tiempo = (np.arange(n) - wfmpre["PT_OFF"]) * wfmpre["XINCR"] + wfmpre["XZERO"]
        return voltaje, tiempo

    def _emit_conn_error(self, msg: str) -> None:
        """Error de conexión o comunicación — desconecta el instrumento lógicamente."""
        self._connected = False
        self.led_rojo.emit()
        self.error.emit(msg)

    def _reanudar_freerun(self, inst: vxi11.Instrument) -> bool:
        try:
            inst.write("ACQ:STOPAFTER RUNSTOP")
            inst.write("ACQ:STATE RUN")
            estado = inst.ask("ACQ:STATE?").strip()
        except Exception as exc:
            if self._connected:
                self._emit_capture_warning(f"No se pudo devolver el osciloscopio a RUN: {exc}")
            return False
        if estado != "1":
            self._emit_capture_warning(
                f"El osciloscopio no volvió a RUN tras la captura (ACQ:STATE? = {estado})."
            )
            return False
        return True

    def _emit_capture_warning(self, msg: str) -> None:
        """Error de captura de waveform — la conexión VXI-11 sigue viva."""
        self.led_amarillo.emit()
        self.error.emit(msg)
