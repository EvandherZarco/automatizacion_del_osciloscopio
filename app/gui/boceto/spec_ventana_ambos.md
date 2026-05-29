# Especificación — ventana_ambos.py

## Propósito
Ventana principal del sistema. Control completo de láser y osciloscopio,
verificación manual de señal, y secuencia automática de medición.
Se abre desde la pantalla de bienvenida al presionar "Ambos".

---

## Flujo de trabajo obligatorio (por diseño)

```
1. Configurar parámetros del láser (láser apagado)
        ↓
2. Iniciar láser (popup de advertencia antes)
        ↓
3. Ajustar osciloscopio con láser encendido
   → Capturar señal de prueba en modo Manual
   → Verificar que la señal en pantalla coincide con el osciloscopio físico
        ↓
4. Guardar señal de prueba
   → Abre diálogo de selección de carpeta
   → Se crea la sesión (CSV + subcarpeta de .npy)
   → Primera medición guardada
        ↓
5. Ir a modo Automático → configurar → Iniciar secuencia
   (usa la sesión ya creada en el paso 4)
```

**Regla crítica:** "Iniciar secuencia" permanece deshabilitado
hasta que exista una sesión activa (paso 4 completado).
Esto garantiza que el usuario siempre verifica la señal antes
de un experimento largo y que la carpeta de destino siempre existe.

**Justificación:** experimentos de 2–3 horas. El overhead de
1–2 min de verificación manual es insignificante frente al riesgo
de perder horas de datos por una sesión no inicializada.

---

## Layout general

```
┌──────────────────────────────────────────────────────────────────┐
│ [← Volver] | [LED]Láser [LED]Oscil [LED]ESP32 S1 S2 S3 S4 | T°C │
├──────────────────────────────────────────────────────────────────┤
│ [Parámetros]  [Medición]  [Visualizar datos]                     │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  (contenido según pestaña activa)                                │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│ [log último evento]                    [⚠ Stop emergencia]       │
└──────────────────────────────────────────────────────────────────┘
```

---

## Barra de conexiones (conn-bar)

| Elemento | Descripción |
|---|---|
| Botón "← Volver" | Regresa a MainWindow (bienvenida). Si láser está en RUN: popup "¿Detener el láser antes de salir?" |
| LED Láser | Gris/Verde/Amarillo/Rojo según estado |
| LED Osciloscopio | Ídem |
| LED ESP32 | Ídem |
| LEDs S1–S4 | Estado individual de cada sensor DS18B20 |
| Temperatura live | Lectura en tiempo real del promedio de sensores activos, en azul `#00bfff` |

---

## Barra inferior (bottombar)

| Elemento | Descripción |
|---|---|
| Chip de log | Último evento: comando enviado, error, resultado de captura |
| "⚠ Stop emergencia" | Siempre activo. Llama `ModoSeguro.activar()`: STOP · E OFF · EO delay 3800 · Burst Continuous |

---

## Pestaña 1 — Parámetros

### Panel Láser (izquierda)

#### Botones Start / Stop
- "Iniciar láser" → muestra popup de advertencia antes de ejecutar `laser.start()`
- "Detener" → llama `laser.stop()`
- Cuando láser = RUN: "Iniciar" deshabilitado, "Detener" activo
- Cuando láser = STOP: "Iniciar" activo, "Detener" deshabilitado

#### Banner de bloqueo
- Visible solo cuando láser = RUN
- Texto: "Detén el láser para modificar parámetros"
- Todos los controles de parámetros y "Aplicar parámetros" deshabilitados

#### Parámetros (solo editables con láser en STOP)

| Control | Tipo | Valores | Registro DLL |
|---|---|---|---|
| Output level | 3 botones radio | E OFF (amber) / E Adjust (azul, default) / E Max (verde) | `Output level` |
| Burst mode | 3 botones radio | Continuous (default) / Burst / Trigger | `Continuous / Burst mode / Trigger burst` |
| Burst length | QSpinBox | 1–30000, deshabilitado si Burst mode = Continuous | `Burst length, pulses` |
| Set cooling T | QDoubleSpinBox | 10.0–50.0 °C, paso 0.1 | `Set cooling temperature` |
| Adj. EO delay | QSpinBox | 800–8000 µs, default 3800 | `Adjustment EO delay` |

#### Monitoreo (solo lectura, siempre visible)
- T agua actual (`Read cooling temperature`)
- T agua objetivo (`Set cooling temperature`)
- Contador de pulsos (`Lamp pulse counter`)
- Se actualizan con QTimer cada 10 s mientras hay conexión

#### Botón "Aplicar parámetros"
- Deshabilitado si: sin conexión O láser = RUN
- Envía todos los parámetros al láser en orden vía DLL

---

### Panel Osciloscopio (derecha)

**Siempre interactivo — independiente del estado del láser.**
El usuario necesita ajustar el osciloscopio mientras el láser dispara
para verificar que la señal es correcta.

| Control | Tipo | Valores |
|---|---|---|
| Canal de señal | 2 botones radio | CH1 / CH2 — sin default, user elige |
| Escala vertical | QComboBox | 1 mV/div … 1 V/div |
| Escala horizontal | QComboBox | 100 ns/div … 100 µs/div |
| Acoplamiento | 2 botones radio | DC (default) / AC |
| Trigger level | QDoubleSpinBox | −10.0 a 10.0 V, paso 0.001 |
| Adquisición | 2 botones radio | Sample (default) / Average |
| N° promedios | QSpinBox | 2–10000, deshabilitado si modo = Sample |

#### Botón "Aplicar parámetros"
- Activo siempre que haya conexión
- Envía todos los parámetros al osciloscopio vía SCPI

---

## Pestaña 2 — Medición

### Panel Manual (izquierda)

**Propósito:** verificar que la señal que ve la computadora
coincide con lo que muestra el osciloscopio físico.

- Descripción breve del propósito (texto informativo)
- Área de previsualización de señal (pyqtgraph pequeño, fondo #0d1117)
  - Muestra el último waveform capturado
  - Labels: canal activo, V/div, s/div
- Botón "Capturar":
  - Siempre activo (si hay conexión al osciloscopio)
  - Llama `oscil.capturar()` en QThread
  - Muestra señal en el plot al terminar
  - Habilita botón "Guardar"
- Botón "Guardar" (deshabilitado hasta captura exitosa):
  - Si NO hay sesión activa:
    - Abre `QFileDialog.getExistingDirectory()` para seleccionar carpeta
    - Llama `almacenamiento.nueva_sesion(nombre_opcional)` en esa carpeta
    - Guarda la medición → **sesión queda activa**
    - Habilita "Iniciar secuencia" en modo automático
  - Si YA hay sesión activa:
    - Guarda directamente sin diálogo
  - Al guardar: botón vuelve a deshabilitarse (requiere nueva captura)

### Panel Automático (derecha)

#### Selector de modo
- Por tiempo / Por temperatura (botones radio)

#### Configuración — Por tiempo
- N° de mediciones: QSpinBox, min 1
- Intervalo (s): QSpinBox, min 15
- Hint: "Tiempo entre el inicio de una medición y la siguiente"

#### Configuración — Por temperatura
- T inicial (°C): QDoubleSpinBox, paso 0.5
- T final (°C): QDoubleSpinBox, paso 0.5
- Paso (°C): QDoubleSpinBox, paso 0.5
- Hint: "Una medición por cada temperatura objetivo, de mayor a menor"

#### Botón "Iniciar secuencia"
- **Deshabilitado hasta que exista sesión activa** (paso 4 del flujo)
- Tooltip cuando deshabilitado: "Realiza y guarda una captura manual primero"
- Al presionar: popup de confirmación → llama `medicion.iniciar()`

#### Botón "Detener"
- Deshabilitado hasta que la secuencia esté corriendo
- Llama `medicion.detener()` → activa modo seguro

#### Área de progreso
- Label con: "Secuencia en curso… | Última: [id] | ⚠ con error: N"
- Se actualiza con cada medición completada

---

## Pestaña 3 — Visualizar datos

Reutiliza `VisualizacionWidget` existente (visualizacion.py):
- Tabla de mediciones con columnas: ID, Timestamp, Temperatura, Modo, Error flag
- Filas con error_flag=1 resaltadas en amarillo
- Plot pyqtgraph de la señal seleccionada
- Panel de metadatos (timestamp, temperatura, modo, error flag)
- Botón "Abrir CSV…" para reimportar sesión
- Botón "Exportar…" para copiar sesión a otra carpeta

---

## Archivos involucrados

| Archivo | Rol |
|---|---|
| `app/gui/ventana_ambos.py` | Esta ventana (renombrar desde main_window.py) |
| `app/gui/visualizacion.py` | Widget de visualización (sin cambios) |
| `app/laser/control_laser.py` | Backend DLL del láser |
| `app/osciloscopio/control_osciloscopio.py` | Backend VXI-11 del osciloscopio |
| `app/temperatura/temperatura.py` | Worker de temperatura |
| `app/almacenamiento/almacenamiento.py` | Sesiones, CSV, .npy |
| `app/modo_seguro/modo_seguro.py` | Stop emergencia |
| `app/conexion/monitoreo.py` | LEDs y reconexión |
| `app/medicion/medicion.py` | Orquestador automático |
| `app/medicion/trigger.py` | Trigger por tiempo / temperatura |

---

## Cambio en Almacenamiento requerido

`almacenamiento.py` actualmente tiene la ruta hardcodeada:
```python
SESIONES_DIR = Path(r"C:\Users\i7\Desktop\automatizacion_Zarco\datos\sesiones")
```

Debe modificarse para que `nueva_sesion()` acepte un parámetro `carpeta_base: Path`
que venga del diálogo de selección del usuario:
```python
def nueva_sesion(self, nombre: str = "", carpeta_base: Path | None = None) -> bool:
```

---

## Notas de implementación

- `ventana_ambos.py` es esencialmente el `main_window.py` actual expandido
- Los módulos (laser, oscil, temp, store, safe, monitor, medicion) se instancian
  aquí o se pasan desde `MainWindow` (bienvenida) — TBD según arquitectura final
- El QTimer de monitoreo de temperatura y métricas del láser debe detenerse
  al cerrar la ventana
- "← Volver" verifica estado del láser antes de cerrar
- La pestaña activa al abrir es siempre "Parámetros"
