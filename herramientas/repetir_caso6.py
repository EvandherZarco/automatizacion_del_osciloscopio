r"""
repetir_caso6.py
Repite el caso 6 del simulador (herramientas\envoltura_caso6.py) N veces en
serie, un proceso por repetición, y guarda la salida de cada una. Cada
repetición tarda unos 14 min; no lance otros procesos pesados mientras corre.

Uso (CMD), desde la raíz del repositorio:
    venv\Scripts\python herramientas\repetir_caso6.py
    venv\Scripts\python herramientas\repetir_caso6.py --n 3 --salida ruta\salida.txt

Por defecto: 3 repeticiones, salida en
"Tesis documento\evidencia\corrida4_caso6_repeticiones.txt" (se sobrescribe).
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ENVOLTURA = Path(__file__).resolve().parent / "envoltura_caso6.py"
SALIDA_POR_DEFECTO = RAIZ / "Tesis documento" / "evidencia" / "corrida4_caso6_repeticiones.txt"


def main() -> int:
    parser = argparse.ArgumentParser(description="Repite el caso 6 del simulador en serie.")
    parser.add_argument("--n", type=int, default=3, help="número de repeticiones (default 3)")
    parser.add_argument("--salida", type=Path, default=SALIDA_POR_DEFECTO)
    args = parser.parse_args()

    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8",
               PYTHONUNBUFFERED="1")
    commit = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], cwd=RAIZ, text=True).strip()
    versiones = subprocess.check_output(
        [sys.executable, "-c",
         "import sys, PySide6; print(sys.version.split()[0], 'PySide6', PySide6.__version__)"],
        text=True).strip()

    args.salida.parent.mkdir(parents=True, exist_ok=True)
    with args.salida.open("w", encoding="utf-8", newline="\n") as f:
        f.write(
            f"# Caso 6 del simulador, {args.n} repeticiones en serie (un proceso por "
            f"repetición), {time.strftime('%Y-%m-%d')}. Commit base {commit}.\n"
            f"# Entorno: Python {versiones}, QT_QPA_PLATFORM=offscreen. La envoltura solo "
            "registra datos (no modifica la prueba); la línea DATOS sale de ella.\n"
        )
        f.flush()
        for i in range(1, args.n + 1):
            t0 = time.time()
            p = subprocess.run([sys.executable, str(ENVOLTURA)], cwd=RAIZ, env=env,
                               capture_output=True, text=True, encoding="utf-8")
            f.write(f"\n##### repetición {i} (inicio "
                    f"{time.strftime('%H:%M:%S', time.localtime(t0))})\n\n{p.stdout}")
            if p.stderr.strip():
                f.write(f"[stderr]\n{p.stderr}")
            f.write(f"[exit={p.returncode} t={time.time() - t0:.0f}s]\n")
            f.flush()
        f.write("\n# FIN\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
