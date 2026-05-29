# Especificación — ventana_laser.py

## Propósito
Ventana independiente para control exclusivo del láser EKSPLA NL303HT-10-SH.
Se abre desde la pantalla de bienvenida al presionar "Láser".
No incluye control de osciloscopio ni temperatura.

---

## Layout general

```
┌─────────────────────────────────────────────────────────────┐
│ [← Volver]  |  [LED] NL303HT-10-SH — COM10    [Conectar]   │
├────────────────────┬────────────────────────────────────────┤
│  PANEL IZQUIERDO   │  PANEL DE PARÁMETROS                   │
│                    │                                        │
│  Estado del láser  │  [banner bloqueo — solo si RUN]        │
│  ┌──────────────┐  │                                        │
│  │  indicador   │  │  Output level                          │
│  │  circular    │  │  [E OFF] [E Adjust] [E Max]            │
│  │  RUN/STOP    │  │                                        │
│  └──────────────┘  │  Burst mode                            │
│  Estado: RUN/STOP  │  [Continuous] [Burst] [Trigger]        │
│                    │                                        │
│  [Iniciar][Detener]│  Burst length  (oculto si Continuous)  │
│                    │  [SpinBox 1–30000 pulsos]              │
│  ── separador ──   │                                        │
│                    │  Set cooling T                         │
│  Monitoreo         │  [SpinBox 10.0–50.0 °C]               │
│  T agua actual     │                                        │
│  T agua objetivo   │  Adj. EO delay                         │
│  Contador pulsos   │  [SpinBox 800–8000 µs]                 │
│  (solo lectura)    │  (seguro = 3800)                       │
│                    │                                        │
│                    │  [Aplicar parámetros]                  │
├────────────────────┴────────────────────────────────────────┤
│  [log último comando]              [⚠ Stop emergencia]      │
└─────────────────────────────────────────────────────────────┘
```

---

## Regla de seguridad — CRÍTICA

**Cuando el láser está en estado RUN:**
- El panel de parámetros completo queda bloqueado (overlay semitransparente encima)
- El botón "Aplicar parámetros" está deshabilitado
- Se muestra un banner de aviso: "Parámetros bloqueados — detén el láser para modificarlos"
- Los únicos botones activos son: "Detener" y "Stop emergencia"

**Cuando el láser está en estado STOP:**
- Todos los parámetros son editables
- "Aplicar parámetros" está habilitado (si hay conexión)
- El banner de aviso desaparece

**Esta regla se aplica sin excepción — no hay forma de modificar parámetros con el láser encendido.**

---

## Barra superior (topbar)

| Elemento | Tipo | Comportamiento |
|---|---|---|
| Botón "← Volver" | QPushButton | Cierra esta ventana, regresa a MainWindow (bienvenida) |
| LED de conexión | QLabel (círculo) | Gris=sin conexión, Verde=conectado, Amarillo=reintentando, Rojo=error |
| Nombre del dispositivo | QLabel | "Sin conexión" / "NL303HT-10-SH" al conectar |
| Chip de estado | QLabel | "Desconectado" / "Conectado — COM10" |
| Botón Conectar/Desconectar | QPushButton | Toggle; llama `laser.conectar()` / `laser.desconectar()` |

---

## Panel izquierdo (ancho fijo ~200px)

### Indicador de estado
- Widget: QLabel circular (border-radius = mitad del tamaño)
- Estado STOP: fondo rojo tenue, icono rayo tachado, texto "STOP"
- Estado RUN: fondo verde tenue, icono rayo, texto "RUN"
- Transición visual al cambiar estado

### Botones Start / Stop
- "Iniciar": QPushButton, borde verde — llama `laser.start()`
- "Detener": QPushButton, borde rojo — llama `laser.stop()`
- Ambos deshabilitados si no hay conexión

### Monitoreo (solo lectura, se actualizan al conectar)
Tres metric cards:

| Métrica | Registro DLL | Tipo |
|---|---|---|
| T agua actual | `Read cooling temperature` | float, °C |
| T agua objetivo | `Set cooling temperature` | float, °C |
| Contador de pulsos | `Lamp pulse counter` | int, disparos |

- Se leen con `laser.read_cooling_temp()` y `laser.read_pulse_counter()` al conectar
- Se actualizan con un QTimer cada 10 s mientras hay conexión
- No son editables desde aquí

---

## Panel de parámetros (derecha)

### Banner de bloqueo
- Visible solo cuando láser = RUN
- Texto: "Parámetros bloqueados — detén el láser para modificarlos"
- Fondo: color warning (amarillo tenue)
- Icono: candado

### Output level
- Tipo: tres QPushButton tipo radio (toggle exclusivo)
- Opciones y registro DLL:

| Botón | Valor DLL | Color activo |
|---|---|---|
| E OFF | `"OFF"` | Amber |
| E Adjust | `"Adjustment"` | Azul (default) |
| E Max | `"Max"` | Verde |

### Burst mode
- Tipo: tres QPushButton tipo radio
- Opciones: `Continuous` (default, azul) / `Burst` / `Trigger`
- Registro DLL: `Continuous / Burst mode / Trigger burst`
- Cuando = Continuous: "Burst length" se deshabilita (opacity 0.35)

### Burst length
- Tipo: QSpinBox
- Rango: 1–30000, default 1
- Registro DLL: `Burst length, pulses`
- Deshabilitado cuando Burst mode = Continuous

### Set cooling T
- Tipo: QDoubleSpinBox
- Rango: 10.0–50.0 °C, paso 0.1, 1 decimal
- Registro DLL: `Set cooling temperature`

### Adj. EO delay
- Tipo: QSpinBox
- Rango: 800–8000 µs, paso 1, default 3800
- Registro DLL: `Adjustment EO delay`
- Nota: valor 3800 = configuración de modo seguro

### Botón "Aplicar parámetros"
- Deshabilitado si: sin conexión O láser en RUN
- Al presionar: envía todos los parámetros al láser en orden:
  1. Output level
  2. Burst mode
  3. Burst length (solo si modo ≠ Continuous)
  4. Set cooling T
  5. Adj. EO delay
- Usa `laser._set_reg()` para cada registro

---

## Barra inferior (bottombar)

| Elemento | Comportamiento |
|---|---|
| Chip de log | Muestra el último comando enviado o resultado |
| "⚠ Stop emergencia" | Llama `ModoSeguro.activar()`: STOP + E OFF + EO delay 3800 + Burst Continuous. Siempre activo. |

---

## Flujo de uso típico

```
[Conectar]
  → laser.conectar() → DLL carga → rcGetFirstDeviceName
  → LED verde, monitoreo se actualiza
  → Parámetros desbloqueados

[Ajustar parámetros] (laser en STOP)
  → Usuario configura Output level, Burst, Cooling T, EO delay
  → [Aplicar parámetros] → _set_reg() × 4-5

[Iniciar]
  → laser.start() → State = RUN
  → Parámetros se bloquean (overlay + banner)
  → Solo "Detener" y "Stop emergencia" activos

[Detener]
  → laser.stop() → State = STOP
  → Parámetros se desbloquean
  → Usuario puede ajustar y volver a iniciar
```

---

## Archivos involucrados

| Archivo | Rol |
|---|---|
| `app/gui/ventana_laser.py` | Esta ventana (por crear) |
| `app/laser/control_laser.py` | Backend DLL (versión nueva) |
| `app/modo_seguro/modo_seguro.py` | Stop emergencia |

---

## Notas de implementación

- No hay osciloscopio, no hay temperatura, no hay modos automáticos
- La ventana se instancia desde `MainWindow` (bienvenida)
- `LaserController` se instancia aquí o se pasa desde la bienvenida (TBD)
- El QTimer de monitoreo debe detenerse al desconectar y al cerrar la ventana
- El overlay de bloqueo se implementa deshabilitando todos los widgets del panel
  o con un QWidget transparente encima que intercepta eventos (según sea más limpio)
- "Volver" debe verificar que el láser esté en STOP antes de cerrar;
  si está en RUN, mostrar advertencia: "El láser sigue encendido. ¿Deseas detenerlo antes de salir?"
