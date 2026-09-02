"""
probar_verificacion.py
Genera cada caso de generar_sesion_prueba.py, corre verificar_sesion.py sobre
él y comprueba que dispare exactamente la alerta esperada: ni de más, ni de
menos, con el código de salida correcto.

Uso:
    python herramientas\\probar_verificacion.py
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from herramientas import generar_sesion_prueba as gen

BASE = PROJECT_ROOT / "datos" / "sesiones_prueba"

# Subcadena que identifica cada alerta de forma inequívoca en la salida de
# verificar_sesion.py. Deben ser mutuamente excluyentes entre sí.
FIRMA_ALERTA = {
    "npy_ausente": "Capturas sin .npy:",
    "sin_reconstruibles": "No hay ninguna captura reconstruible.",
    "saturacion": "Señal saturada",
    "buffer_repetido_misma_escala": "la segunda captura no es una adquisición nueva",
    "buffer_repetido_escala_distinta": "y además escalas distintas",
    "mezcla_escalas": "La sesión mezcla",
    "deriva_onset": "El onset se desplaza",
    "temperatura_estancada": "Todas las capturas reportan la misma temperatura",
    "error_flag": "Capturas con error_flag=1",
    "dispersion_vpp": "Dispersión de Vpp alta a",
}

# Conjunto exacto de alertas que cada caso debe disparar. Los acoplamientos
# (documentados en generar_sesion_prueba.py) se reflejan aquí como conjuntos
# de más de un elemento.
ALERTAS_ESPERADAS = {
    "npy_ausente": {"npy_ausente"},
    "sin_reconstruibles": {"npy_ausente", "sin_reconstruibles"},
    "saturacion": {"saturacion"},
    "buffer_repetido_misma_escala": {"buffer_repetido_misma_escala"},
    "buffer_repetido_escala_distinta": {"buffer_repetido_escala_distinta", "mezcla_escalas"},
    "mezcla_escalas": {"mezcla_escalas"},
    "deriva_onset": {"deriva_onset"},
    "temperatura_estancada": {"temperatura_estancada"},
    "error_flag": {"error_flag"},
    "dispersion_vpp": {"dispersion_vpp"},
    "limpio": set(),
}


@dataclass
class ResultadoCaso:
    nombre: str
    aprobado: bool
    detalle: str = ""


def _correr_verificar_sesion(sesion_dir: Path) -> tuple[int, str]:
    proceso = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "herramientas" / "verificar_sesion.py"),
         str(sesion_dir), "--sin-grafica"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proceso.returncode, proceso.stdout + proceso.stderr


def probar_caso(nombre: str) -> ResultadoCaso:
    print(f"\n=== Caso — {nombre} ===")
    gen.generar_caso(nombre, BASE)
    sesion_dir = BASE / nombre

    codigo, salida = _correr_verificar_sesion(sesion_dir)

    esperadas = ALERTAS_ESPERADAS[nombre]
    codigo_esperado = 1 if esperadas else 0

    presentes = {alerta for alerta, firma in FIRMA_ALERTA.items() if firma in salida}
    inesperadas = presentes - esperadas
    faltantes = esperadas - presentes

    codigo_ok = codigo == codigo_esperado
    contenido_ok = not inesperadas and not faltantes

    aprobado = codigo_ok and contenido_ok
    detalle = (
        f"exit={codigo} (esperado {codigo_esperado}) "
        f"esperadas={sorted(esperadas)} faltantes={sorted(faltantes)} "
        f"inesperadas={sorted(inesperadas)}"
    )
    print(f"  {detalle}")
    return ResultadoCaso(nombre, aprobado, detalle)


def imprimir_resumen(resultados: list[ResultadoCaso]) -> None:
    print("\n" + "=" * 70)
    print("RESUMEN")
    print("=" * 70)
    n_ok = 0
    for r in resultados:
        estado = "OK   " if r.aprobado else "FALLA"
        print(f"[{estado}] {r.nombre}")
        if r.detalle:
            print(f"          {r.detalle}")
        if r.aprobado:
            n_ok += 1
    print("-" * 70)
    print(f"{n_ok}/{len(resultados)} casos correctos")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    resultados = [probar_caso(nombre) for nombre in gen.CASOS_VERIFICACION]
    imprimir_resumen(resultados)
    return 0 if all(r.aprobado for r in resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
