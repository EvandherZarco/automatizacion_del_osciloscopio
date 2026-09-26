"""
probar_guardar_sesion_reabierta.py
Prueba automatizada, sin hardware, de la confirmación antes de escribir en
una sesión abierta con "Abrir CSV…".

Con _sesion_activa siguiendo en vivo a Almacenamiento.activo, Guardar o
una secuencia tras reabrir una sesión agregan filas a ese CSV sin pasar
por el diálogo de carpeta. Ahora se pregunta una vez por reapertura, antes
de la primera escritura, sea un Guardar manual o el inicio de una
secuencia. Un Sí vale para el resto de esa reapertura; un No cancela solo
esa escritura y la siguiente vuelve a preguntar. La respuesta por defecto
es No. El flujo normal, sin nada reabierto, no cambia: no pregunta.

Casos:
    1. Flujo normal: con una sesión creada por Guardar, guardar dos veces
       no muestra la pregunta.
    2. Sesión reabierta, respuesta No: no se escribe fila ni .npy y la
       captura sigue pendiente de guardar.
    3. Tras un No, el siguiente Guardar vuelve a preguntar; con Sí la fila
       se agrega al CSV reabierto y la numeración continúa la del archivo.
    4. Tras el Sí, más Guardar en esa reapertura ya no preguntan.
    5. Tras el Sí, iniciar una secuencia tampoco pregunta y la secuencia
       arranca.
    6. Reabrir otra vez el mismo CSV vuelve a preguntar.
    7. Con la secuencia como primera escritura: pregunta; con No la
       secuencia no arranca; con Sí arranca, y un Guardar posterior ya no
       pregunta.
    8. La pregunta nombra la sesión y su fecha de creación
       (metadatos_sesion.txt) y trae No como botón por defecto.
    9. Sin metadatos_sesion.txt, la fecha sale del prefijo del session_id.
   10. Sin metadatos y con un session_id sin fecha, el diálogo dice
       "fecha de creación no disponible" en vez de inventarla.
   11. Crear una sesión nueva después de reabrir otra deja de preguntar.
   12. La pregunta de sesión reabierta va después de aceptar "Iniciar
       secuencia automática": si esa confirmación se cancela, no se
       pregunta nada, la reapertura queda sin confirmar y el siguiente
       Guardar pregunta.

Uso:
    venv\\Scripts\\python codigos_de_prueba\\ventana_ambos\\probar_guardar_sesion_reabierta.py

Imprime un resumen por caso y termina con código distinto de cero si el
conjunto de casos aprobados no coincide con el esperado.
"""

import csv
import shutil
import sys
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _apoyo import APPDATA_TEMPORAL, CajasCapturadas, app_qt, crear_ventana_ambos, limpiar  # noqa: E402

from PySide6.QtWidgets import QFileDialog, QMessageBox  # noqa: E402

from app.almacenamiento.almacenamiento import Almacenamiento, PaqueteMedicion  # noqa: E402

CASOS_ESPERADOS = {str(i) for i in range(1, 13)}
TITULO = "Guardar en sesión reabierta"
TITULO_SECUENCIA = "Iniciar secuencia automática"

_ventana = None
_cajas: CajasCapturadas | None = None
_preguntas: list[tuple] = []
_respuesta = {"boton": QMessageBox.No, "secuencia": QMessageBox.Ok}


def verificar(condicion, mensaje):
    if not condicion:
        raise AssertionError(mensaje)


@dataclass
class _CapturaFalsa:
    wfmpre: dict
    raw_data: object
    tiempo: object = None
    voltaje: object = None
    error_flag: int = 0
    error_desc: str = ""


def _captura():
    return _CapturaFalsa(
        wfmpre={"XINCR": 1e-8, "XZERO": 0.0, "PT_OFF": 0, "YMULT": 1e-3,
                "YOFF": 0.0, "YZERO": 0.0, "NR_PT": 4},
        raw_data=np.array([1, 2, 3, 4], dtype=np.int16),
        tiempo=np.array([0.0, 1.0, 2.0, 3.0]),
        voltaje=np.array([0.1, 0.2, 0.3, 0.4]),
    )


class _MonitorPasivo:
    error_flag = False
    dispositivos_con_error: list = []

    def set_estado(self, _estado):
        pass


class _LaserEncendido:
    conectado = True

    def leer_parametros(self):
        return {"output_level": None, "eo_delay_us": None, "burst_mode": None}


class _TempConectado:
    puerto = "FAKE"

    def esta_conectado(self):
        return True

    def consultar(self):
        return 21.5, [True] * 4, True

    def consultar_sensores(self):
        return [21.5] * 4, [False] * 4, True


class _MedicionEspia:
    def __init__(self):
        self.llamadas_iniciar = 0

    def iniciar(self, **_kw):
        self.llamadas_iniciar += 1

    def detener(self):
        pass


def _question(*a, **_kw):
    _preguntas.append(a)
    return _respuesta["boton"]


def _warning(*a, **_kw):
    _preguntas.append(a)
    if len(a) > 1 and a[1] == TITULO_SECUENCIA:
        return _respuesta["secuencia"]
    return QMessageBox.Ok


def _preguntas_reabierta() -> list[tuple]:
    return [a for a in _preguntas if len(a) > 1 and a[1] == TITULO]


def _filas(csv_path: Path) -> list[dict]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _carpeta_temporal() -> Path:
    return Path(tempfile.mkdtemp(prefix="sesion_", dir=APPDATA_TEMPORAL))


def _paquete_minimo():
    return PaqueteMedicion(
        timestamp="2026-09-22T19:50:00", temperatura=21.5, modo="manual",
        wfmpre={"XINCR": 1e-8, "XZERO": 0.0, "PT_OFF": 0, "YMULT": 1e-3,
                "YOFF": 0.0, "YZERO": 0.0, "NR_PT": 4},
        raw_data=np.array([1, 2, 3, 4], dtype=np.int16), error_flag=0,
    )


def _sesion_vieja(con_metadatos=True, nombre_sin_fecha=False) -> Path:
    """Crea una sesión en disco con una fila, fuera de la ventana; devuelve su CSV."""
    otra = Almacenamiento()
    verificar(otra.nueva_sesion(carpeta_base=_carpeta_temporal()), "no se pudo crear la sesión vieja")
    otra.guardar(_paquete_minimo())
    meta = otra.csv_path.parent / "metadatos_sesion.txt"
    if con_metadatos:
        texto = meta.read_text(encoding="utf-8").splitlines()
        texto = [("fecha_creacion: 2026-09-22T19:46:24" if l.startswith("fecha_creacion:") else l)
                 for l in texto]
        meta.write_text("\n".join(texto) + "\n", encoding="utf-8")
    else:
        meta.unlink()
    if not nombre_sin_fecha:
        return otra.csv_path
    destino = otra.csv_path.parent / "sesion_prueba.csv"
    shutil.move(otra.csv_path, destino)
    return destino


def _reabrir(csv_path: Path) -> None:
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(csv_path), ""))
    _ventana._tab_viz._reimportar()
    verificar(_ventana._store.csv_path == csv_path, "Abrir CSV… no dejó activa la sesión pedida")
    verificar(_ventana._store.reabierta, "la sesión abierta con Abrir CSV… no quedó marcada como reabierta")


def _capturar_y_guardar() -> None:
    _ventana._on_captura_terminada(_captura(), None)
    _ventana._on_guardar_manual()


def _iniciar_secuencia() -> int:
    """Intenta iniciar una secuencia por temperatura con todo listo; devuelve las llamadas a Medicion.iniciar()."""
    medicion = _MedicionEspia()
    originales = (_ventana._laser, _ventana._temp, _ventana._medicion)
    _ventana._laser, _ventana._temp, _ventana._medicion = _LaserEncendido(), _TempConectado(), medicion
    _ventana._laser_running = True
    _ventana._oscil._connected = True
    _ventana._sel_modo_auto("temperatura")
    _ventana._spin_t_paso.setValue(1.0)
    try:
        _ventana._intentar_iniciar_secuencia()
        if _ventana._secuencia_running:
            _ventana._on_secuencia_ok(0)
    finally:
        _ventana._laser, _ventana._temp, _ventana._medicion = originales
        _ventana._laser_running = False
        _ventana._oscil._connected = False
        _ventana._secuencia_running = False
    return medicion.llamadas_iniciar


# ── Casos ─────────────────────────────────────────────────────────────────────

def caso_1_flujo_normal_no_pregunta():
    carpeta = _carpeta_temporal()
    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(carpeta))
    _preguntas.clear()

    _capturar_y_guardar()
    _capturar_y_guardar()

    verificar(_ventana._store.activo and not _ventana._store.reabierta,
              "Guardar sin sesión no creó una sesión nueva")
    verificar(len(_filas(_ventana._store.csv_path)) == 2, "no se guardaron las dos capturas")
    verificar(_preguntas_reabierta() == [],
              f"el flujo normal mostró la pregunta de sesión reabierta {len(_preguntas_reabierta())} veces")
    print(f"     sesión nueva {_ventana._store.session_id}: 2 filas, ninguna pregunta")


def caso_2_reabierta_no_cancela():
    vieja = _sesion_vieja()
    _reabrir(vieja)
    npy_antes = sorted(p.name for p in vieja.parent.glob("*.npy"))
    _preguntas.clear()
    _respuesta["boton"] = QMessageBox.No

    _capturar_y_guardar()

    verificar(len(_preguntas_reabierta()) == 1, "no se preguntó antes de guardar en la sesión reabierta")
    verificar(len(_filas(vieja)) == 1, f"se escribió una fila pese a responder No ({len(_filas(vieja))} filas)")
    verificar(sorted(p.name for p in vieja.parent.glob("*.npy")) == npy_antes, "apareció un .npy pese a responder No")
    verificar(_ventana._captura_pendiente, "la captura dejó de estar pendiente tras cancelar")
    verificar(_ventana._btn_guardar_manual.isEnabled(), "Guardar quedó deshabilitado tras cancelar")
    print(f"     No: 0 filas nuevas, captura aún pendiente, log={_ventana._lbl_log.text()!r}")


def caso_3_tras_no_vuelve_a_preguntar_y_si_agrega():
    vieja = _ventana._store.csv_path
    sid = _ventana._store.session_id
    _preguntas.clear()
    _respuesta["boton"] = QMessageBox.Yes

    _ventana._on_guardar_manual()

    filas = _filas(vieja)
    verificar(len(_preguntas_reabierta()) == 1, "tras un No, el siguiente Guardar no volvió a preguntar")
    verificar(len(filas) == 2, f"la sesión reabierta tiene {len(filas)} filas, esperadas 2")
    verificar(filas[-1]["medicion_id"] == f"{sid}_m0002",
              f"medicion_id={filas[-1]['medicion_id']!r}, esperado {sid}_m0002")
    print(f"     tras el No volvió a preguntar; Sí: fila {filas[-1]['medicion_id']} agregada")


def caso_4_tras_si_guardar_no_pregunta():
    vieja = _ventana._store.csv_path
    _preguntas.clear()
    _respuesta["boton"] = QMessageBox.No

    _capturar_y_guardar()
    _capturar_y_guardar()

    verificar(_preguntas_reabierta() == [],
              f"tras el Sí se volvió a preguntar {len(_preguntas_reabierta())} veces")
    verificar(len(_filas(vieja)) == 4, f"la sesión reabierta tiene {len(_filas(vieja))} filas, esperadas 4")
    print("     2 Guardar más en la misma reapertura: 0 preguntas, 2 filas")


def caso_5_tras_si_secuencia_no_pregunta():
    _preguntas.clear()
    _respuesta["boton"] = QMessageBox.No

    llamadas = _iniciar_secuencia()

    verificar(_preguntas_reabierta() == [], "tras el Sí, iniciar una secuencia volvió a preguntar")
    verificar(llamadas == 1, f"la secuencia no arrancó (Medicion.iniciar llamado {llamadas} veces)")
    print("     iniciar secuencia en la misma reapertura: sin pregunta, arranca")


def caso_6_reabrir_mismo_csv_vuelve_a_preguntar():
    vieja = _ventana._store.csv_path
    _reabrir(vieja)
    _preguntas.clear()
    _respuesta["boton"] = QMessageBox.No

    _capturar_y_guardar()

    verificar(len(_preguntas_reabierta()) == 1, "reabrir el mismo CSV no volvió a preguntar")
    print("     reabrir el mismo CSV: vuelve a preguntar")


def caso_7_secuencia_como_primera_escritura():
    _reabrir(_sesion_vieja())
    _preguntas.clear()
    _respuesta["boton"] = QMessageBox.No
    con_no = _iniciar_secuencia()
    preguntas_no = len(_preguntas_reabierta())

    _preguntas.clear()
    _respuesta["boton"] = QMessageBox.Yes
    con_si = _iniciar_secuencia()
    preguntas_si = len(_preguntas_reabierta())

    _preguntas.clear()
    _respuesta["boton"] = QMessageBox.No
    filas_antes = len(_filas(_ventana._store.csv_path))
    _capturar_y_guardar()
    preguntas_guardar = len(_preguntas_reabierta())

    verificar(preguntas_no == 1 and con_no == 0,
              f"con No: {preguntas_no} preguntas, Medicion.iniciar llamado {con_no} veces (esperado 1 y 0)")
    verificar(preguntas_si == 1 and con_si == 1,
              f"con Sí: {preguntas_si} preguntas, Medicion.iniciar llamado {con_si} veces (esperado 1 y 1)")
    verificar(preguntas_guardar == 0, "tras confirmar desde la secuencia, Guardar volvió a preguntar")
    verificar(len(_filas(_ventana._store.csv_path)) == filas_antes + 1, "el Guardar posterior no escribió")
    print("     secuencia primero: No → no arranca; Sí → arranca; Guardar después sin pregunta")


def caso_12_cancelar_secuencia_no_confirma():
    _reabrir(_sesion_vieja())
    _preguntas.clear()
    _respuesta["boton"] = QMessageBox.Yes
    _respuesta["secuencia"] = QMessageBox.Cancel
    try:
        llamadas = _iniciar_secuencia()
    finally:
        _respuesta["secuencia"] = QMessageBox.Ok
    titulos = [a[1] for a in _preguntas if len(a) > 1]

    verificar(llamadas == 0, f"la secuencia arrancó pese a cancelar (Medicion.iniciar {llamadas} veces)")
    verificar(TITULO_SECUENCIA in titulos, f"no se mostró la confirmación de secuencia: {titulos}")
    verificar(TITULO not in titulos,
              "se preguntó por la sesión reabierta antes de aceptar la secuencia")
    verificar(not _ventana._reapertura_confirmada, "la reapertura quedó confirmada sin que arrancara nada")

    _preguntas.clear()
    _respuesta["boton"] = QMessageBox.No
    _capturar_y_guardar()
    verificar(len(_preguntas_reabierta()) == 1, "tras cancelar la secuencia, Guardar no preguntó")

    _preguntas.clear()
    _respuesta["boton"] = QMessageBox.Yes
    llamadas = _iniciar_secuencia()
    titulos = [a[1] for a in _preguntas if len(a) > 1]
    verificar(llamadas == 1, "con la secuencia aceptada y Sí, la secuencia no arrancó")
    verificar(titulos.index(TITULO_SECUENCIA) < titulos.index(TITULO),
              f"orden de diálogos {titulos}: la pregunta de sesión reabierta debe ir después")
    print("     Cancelar secuencia: sin pregunta ni confirmación; Guardar luego pregunta; "
          "al aceptar, la pregunta va después")


def caso_8_texto_y_boton_por_defecto():
    _reabrir(_sesion_vieja())
    _preguntas.clear()
    _respuesta["boton"] = QMessageBox.No
    _capturar_y_guardar()

    p = _preguntas_reabierta()
    verificar(len(p) == 1, "no se mostró la pregunta")
    texto = p[0][2]
    verificar(_ventana._store.session_id in texto, f"el texto no nombra la sesión: {texto!r}")
    verificar("creada el 2026-09-22 19:46" in texto, f"el texto no trae la fecha de creación: {texto!r}")
    verificar(len(p[0]) > 4 and p[0][4] == QMessageBox.No,
              f"el botón por defecto no es No: {p[0][4:] if len(p[0]) > 4 else 'sin especificar'}")
    print("     nombra la sesión, 'creada el 2026-09-22 19:46', botón por defecto No")


def caso_9_fecha_desde_session_id():
    _reabrir(_sesion_vieja(con_metadatos=False))
    _preguntas.clear()
    _capturar_y_guardar()

    sid = _ventana._store.session_id
    esperada = f"{sid[0:4]}-{sid[4:6]}-{sid[6:8]} {sid[9:11]}:{sid[11:13]}"
    texto = _preguntas_reabierta()[0][2]
    verificar(f"creada el {esperada}" in texto, f"esperada 'creada el {esperada}', texto: {texto!r}")
    print(f"     sin metadatos: 'creada el {esperada}' desde el session_id")


def caso_10_fecha_no_disponible():
    _reabrir(_sesion_vieja(con_metadatos=False, nombre_sin_fecha=True))
    _preguntas.clear()
    _capturar_y_guardar()

    texto = _preguntas_reabierta()[0][2]
    verificar("fecha de creación no disponible" in texto, f"texto: {texto!r}")
    print("     sin metadatos ni fecha en el id: 'fecha de creación no disponible'")


def caso_11_sesion_nueva_tras_reabrir_no_pregunta():
    store = _ventana._store
    verificar(store.reabierta, "el caso parte de una sesión reabierta")
    verificar(store.nueva_sesion(carpeta_base=_carpeta_temporal()), "no se pudo crear la sesión nueva")
    _preguntas.clear()
    _capturar_y_guardar()

    verificar(not store.reabierta, "nueva_sesion no limpió la marca de reabierta")
    verificar(_preguntas_reabierta() == [], "se preguntó al guardar en una sesión nueva")
    verificar(len(_filas(store.csv_path)) == 1, "la captura no se guardó en la sesión nueva")
    print("     sesión nueva después de reabrir: guarda sin preguntar")


CASOS = (
    ("1", "Flujo normal (nada reabierto): Guardar no pregunta", caso_1_flujo_normal_no_pregunta),
    ("2", "Sesión reabierta + No: no se escribe nada", caso_2_reabierta_no_cancela),
    ("3", "Tras un No vuelve a preguntar; Sí agrega la fila",
     caso_3_tras_no_vuelve_a_preguntar_y_si_agrega),
    ("4", "Tras el Sí, Guardar ya no pregunta", caso_4_tras_si_guardar_no_pregunta),
    ("5", "Tras el Sí, iniciar secuencia no pregunta", caso_5_tras_si_secuencia_no_pregunta),
    ("6", "Reabrir el mismo CSV vuelve a preguntar", caso_6_reabrir_mismo_csv_vuelve_a_preguntar),
    ("7", "Secuencia como primera escritura: pregunta y aplica al resto",
     caso_7_secuencia_como_primera_escritura),
    ("8", "La pregunta nombra sesión y fecha; No por defecto", caso_8_texto_y_boton_por_defecto),
    ("9", "Sin metadatos: fecha desde el session_id", caso_9_fecha_desde_session_id),
    ("10", "Sin ninguna fuente: 'fecha de creación no disponible'", caso_10_fecha_no_disponible),
    ("11", "Sesión nueva tras reabrir: deja de preguntar", caso_11_sesion_nueva_tras_reabrir_no_pregunta),
    ("12", "Cancelar 'Iniciar secuencia' no confirma la reapertura",
     caso_12_cancelar_secuencia_no_confirma),
)


def main():
    global _ventana, _cajas

    print("=" * 72)
    print("Prueba de la confirmación al escribir en sesión reabierta (sin hardware)")
    print("=" * 72)

    _cajas = CajasCapturadas()
    _ventana = crear_ventana_ambos(_cajas)
    QMessageBox.question = staticmethod(_question)
    QMessageBox.warning = staticmethod(_warning)
    _ventana._monitor = _MonitorPasivo()
    app = app_qt()

    aprobados = set()
    for clave, descripcion, funcion in CASOS:
        print(f"[{clave}] {descripcion}")
        try:
            funcion()
            app.processEvents()
        except Exception as exc:
            print(f"     FALLO: {type(exc).__name__}: {exc}")
            if not isinstance(exc, AssertionError):
                traceback.print_exc()
            continue
        aprobados.add(clave)

    print("-" * 72)
    faltantes = sorted(CASOS_ESPERADOS - aprobados, key=int)
    print(f"Aprobados : {sorted(aprobados, key=int)}")
    print(f"Esperados : {sorted(CASOS_ESPERADOS, key=int)}")
    if faltantes:
        print(f"Fallaron  : {faltantes}")
        print("RESULTADO: FALLO")
        return 1
    print("RESULTADO: TODOS LOS CASOS APROBADOS")
    return 0


if __name__ == "__main__":
    try:
        codigo = main()
    finally:
        limpiar()
    sys.exit(codigo)
