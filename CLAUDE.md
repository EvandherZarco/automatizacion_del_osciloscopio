# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

PySide6 desktop app that automates a photoacoustic-measurement setup for a UNAM thesis: a Tektronix TDS5052B oscilloscope (via VXI-11/Ethernet), an EKSPLA NL303HT-10-SH laser (via a vendor RS-232 DLL), and an ESP32 WROOM-32 with 4× DS18B20 temperature sensors (via USB serial). It runs manual single-shot captures and fully automatic measurement sequences (by fixed time interval or by temperature thresholds), saving each capture as CSV metadata + `.npy` waveform.

Everything is Windows-only (the laser control depends on a 64-bit Windows DLL loaded via `ctypes`).

## Commands

```bat
:: run the app (activates venv, launches GUI)
iniciar_app.bat

:: equivalent manually
venv\Scripts\activate
python main.py
```

Dependencies are pinned in `requirements.txt` (`pip install -r requirements.txt` inside `venv`). There is no lint/test/build tooling configured — `codigos_de_prueba/` scripts are standalone manual hardware scripts run directly with `python <script>.py`, not a pytest suite, and are meant to be run against real connected hardware.

`codigos_de_prueba/osciloscopio/exportar_mat.py` converts a saved session (`.csv` + `.npy` files) to per-measurement `.mat` files for MATLAB: `python exportar_mat.py "ruta\a\la\sesion"`.

## Architecture

Entry point `main.py` launches `BienvenidaWindow` (`app/gui/bienvenida.py`), which lets the user open one of three top-level windows: laser-only, oscilloscope-only, or the combined system (`app/gui/ventana_ambos.py` — this is where the real orchestration lives; the other two are subsets of the same pattern). Each child window owns its own module instances and shows/hides `BienvenidaWindow` via a `volver` (go-back) signal.

### Module layout (`app/`)

Each hardware/domain concern is an isolated package with a `QObject`-based controller:

- `laser/control_laser.py` — `LaserController`. Loads `complementos/REMOTECONTROL64.dll` via `ctypes`, connects over RS-232 (`app.config.LASER_COM_PORT`), and reads/writes named registers (`State`, `Output level`, `Adjustment EO delay`, etc. — see `REMOTECONTROL.CSV` for the full register list). All hardware I/O guarded by a `QMutex`.
- `osciloscopio/control_osciloscopio.py` — `OsciloscopioController`. Talks SCPI over VXI-11 (`python-vxi11`) to `app.config.OSCIL_HOST`. **Requires the TekScope app to be running on the oscilloscope's internal OS** — this is a hard hardware prerequisite, not optional. Has three acquisition modes: manual (`NUMAVG=1`), "tiempo" (scope averages `NUMAVG_TIEMPO` shots internally, Python polls `ACQ:STATE?` until done), and "temperatura" (`NUMAVG=10000` so the scope never auto-stops; Python opens/closes the acquisition window with `acq_run()` / `acq_stop_and_capture()`).
- `temperatura/temperatura.py` — `TempWorker`. Reads a continuous stream from an ESP32 WROOM-32 over serial in its own `QThread` loop, one line per sample: five comma-separated fields, no prefix — `promedio,s1,s2,s3,s4\n`, where `promedio` is the average temperature (float) and `s1`–`s4` are per-sensor presence flags (`1`/`0`). Other modules call the thread-safe `consultar()` to get the latest reading plus a freshness flag (readings older than `FRESCURA_MAX_S` are considered stale). The board drives four independent OneWire buses (one DS18B20 per bus) on GPIO 16, 17, 18, 19; the KY-001 modules carry their own 4.7 kΩ pull-up.
- `medicion/trigger.py` + `medicion/medicion.py` — automatic sequence orchestration. `TriggerWorker` runs a blocking loop in its own `QThread` and emits timing signals (`medir_ahora` for time mode; `iniciar_acumulacion`/`detener_y_capturar` for temperature mode, which steps through a descending list of target temperatures). `MedicionWorker` (separate `QThread`) reacts to those signals to actually drive the oscilloscope and persist results. `Medicion` is the facade the GUI calls (`iniciar()`/`detener()`); it wires up both `QThread`s and their cross-thread queued-signal connections and tears them down again per sequence. The laser is started once for the whole sequence, not per-measurement.
- `almacenamiento/almacenamiento.py` — `Almacenamiento`. One session = one directory under `datos/sesiones/<session_id>/` containing a session CSV (fixed `CSV_HEADER`, validated on `abrir_sesion`) and one `.npy` per measurement (raw ADC samples; `wfmpre` scaling params like `XINCR`/`YMULT` are stored in the CSV row, not the `.npy`).
- `modo_seguro/modo_seguro.py` — `ModoSeguro`. Single entry point (`activar()`) that always sends all four laser safety commands (STOP, EO delay 3800, E OFF, Burst Continuous) even if one fails, and reports which ones failed. Called from GUI e-stop, end/failure of a measurement sequence, unrecoverable reconnection failure, and manual-mode inactivity timeout. **Never touches the oscilloscope.**
- `conexion/monitoreo.py` — `MonitoreoConexion`. Polls all three devices on a `QTimer` (interval shortens between measurements, pauses entirely during a capture via `pausar_pings`/`reanudar_pings`), and on any disconnect spins up a short-lived `QThread` (`_ReconexionWorker`) to retry reconnecting without blocking the GUI. If a *critical* device (laser or oscilloscope — not the ESP32) fails to reconnect after `MAX_REINTENTOS`, it triggers `ModoSeguro`.
- `gui/` — PySide6 windows and widgets (`bienvenida.py`, `ventana_laser.py`, `ventana_osciloscopio.py`, `ventana_ambos.py`, `visualizacion.py` for waveform plotting via `pyqtgraph`, `theme.py` for the shared dark stylesheet and LED-indicator helpers).

### Cross-cutting patterns worth knowing before changing this code

- **Threading model**: every long-running or blocking hardware operation (device reconnection, the measurement trigger loop, the measurement worker, temperature streaming) runs in its own `QThread` with a `QObject` moved onto it via `moveToThread`; communication back to the GUI thread is via Qt signals (auto-queued across threads). When adding new cross-thread interaction, follow the existing pattern in `medicion/medicion.py` (`Medicion.iniciar`) rather than calling into a worker's methods directly from another thread.
- **Hardware access is mutex-guarded**: `LaserController` and `OsciloscopioController` each hold one `QMutex` around all device I/O — don't bypass it by calling private `_dll`/`_inst` methods directly from new code.
- **Config**: all machine-specific hardware addresses/ports (`TEMP_COM_PORT`, `LASER_COM_PORT`, `OSCIL_HOST`) live in `app/config.py` and must be edited there before running against different hardware.
- **`error_flag`**: measurements are still saved on partial failure (capture timeout, stale/missing temperature, exceeded interval) but flagged `error_flag=1` with a human-readable `error_desc` in the CSV — don't silently drop a failed capture; follow the existing flag-and-record convention.

## Convenciones del proyecto

- PyVISA **no** funciona con el TDS5052B — se usa `python-vxi11` exclusivamente. PyVISA sigue listado en `requirements.txt` como residuo de pruebas anteriores; no proponerlo nunca como alternativa.
- PySide6 usa `Signal` (no `pyqtSignal`) para declarar señales.
- Toda la GUI mantiene tema oscuro (PySide6 + `pyqtgraph`). No introducir estilos claros.
- El código no lleva comentarios meta, TODOs, ni indicaciones al programador. Debe leerse como si lo hubiera escrito una persona.
- `DATA:STOP` debe calcularse dinámicamente a partir del record length real, nunca hardcodearse — un valor fijo ya provocó capturas contaminadas con ruido pre-trigger.
- Las carpetas `complementos/` y `datos/` no se modifican nunca.
- Para ejecutar Python usar el intérprete de `venv\Scripts\`, no el del sistema.
