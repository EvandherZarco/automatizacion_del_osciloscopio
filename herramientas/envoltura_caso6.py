r"""
envoltura_caso6.py
Corre el caso 6 de simular_barrido.py (rebote térmico en el umbral) tal cual y
añade una línea DATOS con lo que el caso no imprime: temperatura y t real de
apertura del 45 °C, duración real y simulada, t_ini_real y fin del rebote, y si
el cierre fue posterior al rebote. No modifica la prueba: solo observa.

Uso (CMD), desde la raíz del repositorio:
    venv\Scripts\python herramientas\envoltura_caso6.py

Sale con 0 si el caso aprueba y con 1 si falla. Lo lanza repetir_caso6.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import simular_barrido as s  # noqa: E402
from PySide6.QtCore import QCoreApplication  # noqa: E402

DURACION_REBOTE_S = 3.0
OBJETIVO_C = 45.0

app = QCoreApplication([])
estado = {"colector": None, "rebote": None}

_init_colector = s.ColectorSenales.__init__


def _init_registrando(self, *a, **k):
    _init_colector(self, *a, **k)
    estado["colector"] = self


s.ColectorSenales.__init__ = _init_registrando


class _DobleRegistrando(s.DobleTemperaturaSimulada):
    @property
    def rebote_real(self):
        return self.__dict__.get("_rebote")

    @rebote_real.setter
    def rebote_real(self, valor):
        self.__dict__["_rebote"] = valor
        if valor is not None:
            estado["rebote"] = valor


s.DobleTemperaturaSimulada = _DobleRegistrando

resultado = s.caso_6_rebote_termico()
s.imprimir_resumen([resultado])

ventana = next(
    (v for v in estado["colector"].ventanas if v["objetivo"] == OBJETIVO_C), None
)
ini = estado["rebote"][0] if estado["rebote"] else None
if ventana:
    fin_rebote = None if ini is None else ini + DURACION_REBOTE_S
    despues = None if ini is None else ventana["t_real_fin"] >= fin_rebote
    print(
        f"DATOS {OBJETIVO_C:g} C: temp_apertura={ventana['temp_apertura']:.2f} "
        f"t_real_apertura={ventana['t_real_ini']:.2f}s "
        f"t_real_cierre={ventana['t_real_fin']:.2f}s "
        f"duracion_real={ventana['duracion_real']:.2f}s "
        f"duracion_sim={ventana['duracion_sim']:.1f}s "
        f"t_ini_real_rebote={ini if ini is None else round(ini, 2)}s "
        f"fin_rebote={fin_rebote if fin_rebote is None else round(fin_rebote, 2)}s "
        f"cierre_despues_de_rebote={despues} aprobado={resultado.aprobado}"
    )
else:
    print(
        f"DATOS {OBJETIVO_C:g} C: sin ventana; t_ini_real_rebote={ini} "
        f"aprobado={resultado.aprobado}"
    )
sys.exit(0 if resultado.aprobado else 1)
