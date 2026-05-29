# Especificación — ventana_osciloscopio.py

## Propósito
Ventana independiente para control exclusivo del osciloscopio Tektronix TDS5052B.
Se abre desde la pantalla de bienvenida al presionar "Osciloscopio".
No incluye control de láser ni temperatura.

---

## Layout general

```
┌─────────────────────────────────────────────────────────────┐
│ [← Volver]  |  [LED] TDS5052B — 192.168.1.100  [Conectar]  │
├──────────────────────────────────┬──────────────────────────┤
│                                  │  PANEL DE CONTROLES      │
│   ÁREA DE SEÑAL (waveform)       │                          │
│   fondo oscuro #0d1117           │  Canal de señal          │
│   grid verde tenue               │  [CH1] [CH2]  ← sin     │
│   curva azul #00bfff             │  default, user elige     │
│   canal activo arriba izq.       │                          │
│   V/div arriba der.              │  ── separador ──         │
│   s/div abajo centro             │                          │
│                                  │  Escala vertical         │
│   Sin canal → overlay gris       │  [dropdown V/div]        │
│   Sin conexión → overlay gris    │                          │
│                                  │  Escala horizontal       │
│                                  │  [dropdown s/div]        │
│                                  │                          │
│                                  │  ── separador ──         │
│                                  │                          │
│                                  │  Acoplamiento            │
│                                  │  [DC] [AC]               │
│                                  │                          │
│                                  │  Nivel de trigger        │
│                                  │  [QDoubleSpinBox] V      │
│                                  │                          │
│                                  │  ── separador ──         │
│                                  │                          │
│                                  │  Adquisición             │
│                                  │  [Sample] [Average]      │
│                                  │                          │
│                                  │  N° de promedios         │
│                                  │  [QSpinBox 2–10000]      │
│                                  │  (deshabilitado si       │
│                                  │   modo = Sample)         │
├──────────────────────────────────┴──────────────────────────┤
│  [Capturar]  [Guardar señal*]          [⚠ Stop emergencia]  │
└─────────────────────────────────────────────────────────────┘
* Guardar señal deshabilitado hasta que exista una captura en memoria.
```

---

## Barra superior (topbar)

| Elemento | Tipo | Comportamiento |
|---|---|---|
| Botón "← Volver" | QPushButton | Cierra esta ventana, regresa a MainWindow (bienvenida) |
| LED de conexión | QLabel (círculo) | Gris=sin conexión, Verde=conectado, Amarillo=reintentando, Rojo=error |
| Nombre del dispositivo | QLabel | "Sin conexión" / "TDS5052B" al conectar |
| Chip de estado | QLabel | "Desconectado" / "Conectado — 192.168.1.100" |
| Botón Conectar/Desconectar | QPushButton | Toggle; llama `oscil.conectar()` / `oscil.desconectar()` |

---

## Panel de controles (derecha, ancho fijo ~220px)

### Canal de señal
- Tipo: dos QPushButton tipo radio (toggle exclusivo)
- Opciones: CH1, CH2
- **Sin valor por defecto** — ambos inactivos al abrir
- Al seleccionar: llama `oscil.set_canal(canal)`
- Mientras no se seleccione canal: área de señal muestra overlay "Selecciona un canal"

### Escala vertical
- Tipo: QComboBox
- Opciones: 1 mV/div, 2, 5, 10, 20, 50 mV/div, 100, 200, 500 mV/div, 1 V/div
- Al cambiar: envía comando SCPI `CHx:SCALE valor`

### Escala horizontal
- Tipo: QComboBox
- Opciones: 100 ns/div, 200 ns, 500 ns, 1 µs, 2 µs, 5 µs, 10 µs, 20 µs, 50 µs, 100 µs/div
- Al cambiar: envía comando SCPI `HORizontal:SCAle valor`

### Acoplamiento
- Tipo: dos QPushButton tipo radio
- Opciones: DC, AC
- Default: DC
- Al cambiar: envía comando SCPI `CHx:COUPling DC|AC`

### Nivel de trigger
- Tipo: QDoubleSpinBox
- Rango: −10.0 a 10.0 V, paso 0.001, 3 decimales
- Al confirmar (Enter o perder foco): envía `TRIGger:MAIn:LEVel valor`

### Adquisición
- Tipo: dos QPushButton tipo radio
- Opciones: Sample, Average
- Default: Sample
- Al cambiar: envía `ACQ:MODE SAMPLE|AVERAGE`

### N° de promedios
- Tipo: QSpinBox
- Rango: 2–10000, default 100
- **Deshabilitado cuando modo = Sample**
- Al confirmar: envía `ACQ:NUMAVG valor`

---

## Área de señal (izquierda, fondo #0d1117)

- Widget: pyqtgraph PlotWidget con fondo #0d1117
- Grid: líneas verdes tenues, alpha 0.15
- Curva: pen azul #00bfff, ancho 1.5px
- Ejes: tiempo en µs (eje X), voltaje en mV (eje Y)
- Labels flotantes sobre la gráfica:
  - Arriba izquierda: canal activo (ej. "CH1") en azul
  - Arriba derecha: V/div seleccionado en verde
  - Abajo centro: s/div seleccionado en gris
- Estados especiales:
  - Sin canal seleccionado → overlay semitransparente + texto "Selecciona un canal para comenzar"
  - Sin conexión → overlay semitransparente + texto "Sin conexión al osciloscopio"

---

## Barra inferior (bottombar)

| Elemento | Estado inicial | Comportamiento |
|---|---|---|
| Botón "Capturar" | Habilitado siempre | Llama `oscil.capturar()` en QThread; muestra señal en gráfica al terminar |
| Botón "Guardar señal" | **Deshabilitado** | Se habilita solo después de una captura exitosa; llama `Almacenamiento.guardar()` |
| Botón "⚠ Stop emergencia" | Habilitado siempre | Llama `ModoSeguro.activar()` — solo afecta el láser si está presente |

---

## Flujo de captura (modo manual)

```
Usuario presiona [Capturar]
  → Botón Capturar se deshabilita (evitar doble click)
  → _ManualCapturaWorker corre en QThread
  → oscil.capturar() → ACQ:STATE RUN → polling → WFMPRE? → CURVE?
  → Worker emite terminado(CapturaOscil | None)
  → GUI recibe resultado:
      Si ok  → grafica señal → habilita [Guardar señal] → habilita [Capturar]
      Si None → muestra QMessageBox de error → habilita [Capturar]

Usuario presiona [Guardar señal]
  → Almacenamiento.guardar(PaqueteMedicion)
  → Botón vuelve a deshabilitarse (requiere nueva captura para volver a guardar)
  → Mensaje de confirmación breve en status chip
```

---

## Archivos involucrados

| Archivo | Rol |
|---|---|
| `app/gui/ventana_osciloscopio.py` | Esta ventana (por crear) |
| `app/osciloscopio/control_osciloscopio.py` | Backend hardware |
| `app/almacenamiento/almacenamiento.py` | Guardar .npy + CSV |
| `app/modo_seguro/modo_seguro.py` | Stop emergencia |

---

## Notas de implementación

- La ventana se instancia desde `MainWindow` (bienvenida) al hacer click en "Osciloscopio"
- Al cerrar con "← Volver": `self.close()` — la ventana de bienvenida sigue abierta
- El `OsciloscopioController` se instancia aquí o se pasa desde la bienvenida (TBD)
- No hay temperatura, no hay láser, no hay modos automáticos en esta ventana
- Los comandos SCPI de escala vertical/horizontal requieren agregar métodos a `control_osciloscopio.py` (set_vdiv, set_tdiv, set_coupling, set_trigger_level) — pendiente
