# Especificación: simulador de barrido de temperatura

Objetivo: ejercitar el modo temperatura completo sin ESP32, láser ni
osciloscopio, comprimiendo en minutos un barrido que en el laboratorio toma
horas. Se busca detectar fallas de la máquina de estados antes de la sesión del
1 de septiembre, que es la penúltima disponible.

Ubicación propuesta: `herramientas/simular_barrido.py`

No se debe simular el puerto serie ni el firmware del ESP32. `TriggerWorker`
consume únicamente `temp_worker.consultar()`, así que basta un objeto que
exponga ese método con la misma firma.

---

## 1. Doble de temperatura

Debe replicar exactamente la interfaz de `TempWorker.consultar()`:

```
consultar() -> tuple[float | None, list[bool], bool]
```

Devuelve `(temperatura_promedio, estado_de_los_cuatro_sensores, es_fresco)`.

Comportamiento:

- Curva de enfriamiento de Newton: `T(t) = T_amb + (T0 - T_amb) * exp(-t / tau)`
- Parámetros configurables: `T0`, `T_amb`, `tau`, y un **factor de compresión**
  que multiplica el tiempo transcurrido antes de evaluar la curva. Con factor
  100, tres horas de enfriamiento ocurren en poco menos de dos minutos.
- Ruido gaussiano configurable sobre la lectura, con desviación por omisión de
  0.05 °C, del orden del ruido real del DS18B20.
- Cuantización a múltiplos de 0.0625 °C, que es la resolución real del sensor.
- Modo de falla: dado un intervalo `(t_inicio, t_fin)` en tiempo simulado,
  `consultar()` devuelve `(None, [False]*4, False)` dentro de ese intervalo,
  reproduciendo una desconexión del ESP32.
- Modo de rebote: opción para inyectar un ascenso transitorio de temperatura
  de magnitud y duración configurables, que permita verificar que la ventana de
  integración no se cierra por una fluctuación aislada.

## 2. Colector de señales

Un `QObject` conectado a las señales del trigger que registre, para cada una,
el instante real y el instante simulado, además de la temperatura vigente.

Señales a registrar: `iniciar_acumulacion`, `detener_y_capturar`,
`advertencia`, `secuencia_terminada`.

Al final debe imprimir una tabla por punto objetivo con:

| Objetivo | T de apertura | T de cierre | Duración de ventana | Pulsos estimados |

Los pulsos estimados se calculan como `duracion_simulada * 10 Hz`, que es lo que
el sistema real guardaría en el CSV.

## 3. Casos de prueba

Cada caso indica su criterio de aprobación. El script debe ejecutarlos en
secuencia e informar al final cuántos pasaron.

### Caso 1 — Barrido nominal
`T0 = 62`, `T_amb = 21`, `tau` comprimida, objetivos de 60 a 30 con paso 5.

Aprueba si: se generan siete ventanas, en orden descendente de temperatura,
ninguna con duración menor a la mitad de la esperada teóricamente, y todas con
`pulsos_estimados > 0`.

### Caso 2 — Arranque con la muestra ya tibia
`T0 = 47`, objetivos de 60 a 30 con paso 5.

Aprueba si: se emite una advertencia que menciona los objetivos omitidos, se
omiten exactamente 60, 55 y 50, y las ventanas restantes son normales.

Este caso cubre el defecto de objetivos consumidos en cascada: sin el descarte,
los tres primeros producirían ventanas de duración nula.

### Caso 3 — Ruido elevado
Ruido de 0.3 °C de desviación, barrido nominal.

Aprueba si: el número de ventanas sigue siendo siete. Cualquier exceso indica
disparo falso; cualquier faltante indica cruce perdido.

### Caso 4 — Desconexión breve
Corte de lecturas de 45 segundos de tiempo real a mitad del barrido.

Aprueba si: se emite la advertencia de lectura interrumpida, luego la de lectura
restablecida, y el barrido completa todos sus puntos.

### Caso 5 — Desconexión permanente
Corte de lecturas que no se recupera. Para esta prueba, sobrescribir desde el
script `trigger.TIMEOUT_ADVERTENCIA_S = 2` y `trigger.TIMEOUT_ABORTO_S = 6`, de
modo que no haga falta esperar cinco minutos reales.

Aprueba si: se emite la advertencia de fin de secuencia y `secuencia_terminada`
se dispara, en lugar de quedar el hilo esperando indefinidamente.

### Caso 6 — Rebote térmico en el umbral
Ascenso transitorio de 0.15 °C justo al cruzar el umbral de cierre de un punto.

Aprueba si: la ventana de ese punto no se cierra durante el rebote y su duración
resulta comparable a la de los puntos vecinos.

### Caso 7 — Muestra por debajo de todos los objetivos
`T0 = 25`, objetivos de 60 a 30.

Aprueba si: se emite la advertencia correspondiente y la secuencia termina sin
generar ninguna ventana.

## 4. Nivel dos: cadena completa

Además del trigger aislado, ejecutar un barrido nominal a través de la clase
`Medicion` con dobles de láser y osciloscopio, y con `Almacenamiento` real
apuntando a una carpeta temporal.

Los dobles deben responder a lo que `MedicionWorker` invoca:

- Láser: `start()`, `leer_parametros()`, y el objeto de modo seguro con
  `activar()`
- Osciloscopio: `configurar_modo_temperatura()`, `acq_run()`,
  `acq_stop_and_capture()`, `cancelar_espera()`
- La captura devuelta debe traer `wfmpre` con las claves reales, incluidas
  `CH_SCALE` y `HOR_SCALE`, y un `raw_data` sintético

Aprueba si: el CSV resultante tiene una fila por punto objetivo, todas las filas
traen `pulsos_estimados` mayor a cero, la columna de temperatura desciende
monótonamente, y el archivo abre correctamente con `Almacenamiento.abrir_sesion`.

## 5. Requisitos de forma

- El script debe correr con `python herramientas\simular_barrido.py` desde la
  raíz del proyecto, sin argumentos, y ejecutar los siete casos más el nivel dos.
- Debe aceptar un argumento opcional para correr un solo caso por número.
- No debe escribir en `datos/sesiones/`; usar `tempfile` para las sesiones
  generadas durante las pruebas.
- No debe modificar ningún archivo de `app/`. Si un caso falla, el diagnóstico
  se discute antes de corregir el código de producción.
- Salida por consola legible, con el resumen de casos aprobados al final.

## 6. Nota sobre los tiempos

Los umbrales `TIMEOUT_ADVERTENCIA_S` y `TIMEOUT_ABORTO_S` de `trigger.py` operan
en tiempo real y no se ven afectados por el factor de compresión, que solo actúa
sobre la curva de enfriamiento. Los casos que necesiten ejercitarlos deben
sobrescribir esas constantes a nivel de módulo desde el script de prueba, y
restaurarlas al terminar.

Igualmente, `POLL_TEMP_S` sigue siendo de medio segundo real. Con factor de
compresión alto, cada sondeo cubre un salto de temperatura grande, lo que
constituye por sí mismo una prueba severa: si el barrido sobrevive a saltos
mucho mayores que los reales, con más razón funcionará en el laboratorio.
