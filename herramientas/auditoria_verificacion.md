# Auditoría de las herramientas de verificación

Alcance: `herramientas\verificar_sesion.py`, `herramientas\simular_barrido.py`,
`herramientas\generar_sesion_prueba.py`, `herramientas\probar_advertencias_gui.py`,
`herramientas\probar_verificacion.py`.
Solo inventario de qué validan realmente estas herramientas y qué queda fuera de su
alcance. No incluye estilo, nombres ni nada bajo `app/` salvo como referencia para
entender qué se está verificando.

Auditado contra el commit `774cf8e397ce96e0586b2f88e1a5895690d10444` (2026-09-01).
Es una fotografía de ese estado del código, no una garantía permanente: si
`verificar_sesion.py`, `simular_barrido.py`, `generar_sesion_prueba.py` o
`probar_advertencias_gui.py` cambian después de ese commit, este documento puede
quedar desactualizado sin que nada lo señale — como ya le pasó a la fila de
dispersión de Vpp más abajo (auditada contra el commit anterior, `ee475041a218a3985b9b3886a9ecda74a55c662a`,
antes de que existiera el generador de casos de esta sección).

---

## `verificar_sesion.py`

### Tabla de chequeos

| Chequeo | Qué detecta | Qué deja pasar |
|---|---|---|
| Capturas sin `.npy` (`faltantes`) | Fila del CSV cuyo `archivo_npy` está vacío o el archivo no existe en la carpeta de la sesión | Un `.npy` que existe pero está corrupto, truncado, o pertenece a otra medición (nombre correcto, contenido equivocado) |
| Señal saturada (`saturada`) | Que el valor absoluto máximo de las cuentas ADC crudas alcance el `MARGEN_SATURACION` (98%) del rango del dtype (`np.iinfo(dtype).max`) | Saturación asimétrica cerca del límite pero por debajo del 98% (recorte suave); saturación en el lado negativo si el dtype es unsigned (`iinfo(...).max` no tiene signo negativo simétrico); clipping causado aguas arriba del ADC (p. ej. en el amplificador) que no toca el techo digital |
| Buffer repetido (`combinations` sobre `raw`) | Que dos capturas cualesquiera de la sesión tengan el arreglo `raw` bit a bit idéntico (indicio de que el osciloscopio devolvió el buffer anterior sin adquirir) | Buffers casi idénticos pero no exactamente iguales (p. ej. con 1 LSB de diferencia por ruido de cuantización); repetición de tres o más capturas se reporta como pares sueltos, no como grupo; el caso en que la señal real es plana y dos adquisiciones distintas coinciden por casualidad (falso positivo no filtrado) |
| Mezcla de escalas en la sesión (`agrupar_por_escala`, `len(grupos) > 1`) | Que la sesión contenga capturas con más de una combinación `(XINCR, YMULT, NR_PT)` | Cambios de escala que coincidan por casualidad en esos tres valores pero difieran en otros parámetros de `wfmpre` (p. ej. `YOFF`, `YZERO`, `PT_OFF`, `CH_SCALE`, `HOR_SCALE`) — esos no entran en la tupla `escala` y no se detectan como cambio |
| Deriva del onset dentro de un grupo de escala | Que, dentro de un mismo grupo de escala con ≥3 onsets válidos, el rango (`max-min`) de los tiempos de onset supere `TOLERANCIA_ONSET_FRAC` (2%) del span temporal del registro | Deriva del onset **entre** grupos de escala distintos (el chequeo agrupa por escala, así que nunca compara onsets de un grupo contra otro); grupos con menos de 3 onsets válidos (se saltan silenciosamente); deriva progresiva y monótona que se mantenga dentro del 2% pero sea sistemática (tendencia real, no solo dispersión) |
| Temperatura constante en toda la sesión | Que todas las capturas válidas reporten exactamente la misma temperatura (indicio de ESP32 congelado) | Temperatura que varía mínimamente pero de forma no realista (p. ej. oscila entre 2 valores en vez de quedarse fija); congelamiento parcial (el ESP32 se congela a mitad de sesión, dejando dos bloques de temperaturas distintas pero cada uno constante) |
| `error_flag=1` en el CSV | Capturas que el propio sistema ya marcó con error al guardarlas | Cualquier fallo que el sistema de adquisición no haya detectado y por tanto nunca haya puesto en `error_flag` |
| Dispersión de Vpp por temperatura y escala (`chequeos`, línea ~218) | Que algún grupo `(temperatura redondeada a 0.1 °C, escala)` con `media(Vpp) != 0` tenga dispersión relativa (`100·σ/media`) mayor que `DISPERSION_ACEPTABLE_PCT` (5%) — **sí** agrega una entrada a `alertas` y por tanto **sí** afecta el código de salida (`return 1 if alertas else 0`) | Grupos con un único punto (σ=0, nunca se marca aunque el valor sea anómalo); grupos cuya media de Vpp sea exactamente 0 (se saltan explícitamente, `if v.mean() == 0: continue`); dispersión alta compensada por redondeo a 0.1 °C que junta puntos de temperaturas ligeramente distintas; `resumen_por_temperatura` marca lo mismo con `<-- revisar` en la tabla visual, pero es una función aparte — puramente informativa, no contribuye a `alertas` por sí misma |

### Criterios que dependen de umbral, fracción o agrupamiento

- **`MARGEN_SATURACION = 0.98`**: cualquier captura cuyo pico ADC quede entre el 98% y el 100% del rango se marca saturada; por debajo de 98% nunca se marca, sin importar cuán cerca esté.
- **`UMBRAL_ONSET_SIGMA = 8.0`**: el onset se define como el primer instante en que `|voltaje| > 8·ruido_std` (ruido estimado sobre el primer 5% del registro). Señales de baja amplitud cuyo pico no llega a 8σ nunca producen onset (`onset_us = NaN`), y esas capturas quedan fuera del chequeo de deriva de onset sin generar alerta.
- **`DISPERSION_ACEPTABLE_PCT = 5.0`**: umbral tanto de la entrada en `alertas` (línea ~218 de `chequeos()`, sí cambia el código de retorno) como del marcador `<-- revisar` de la tabla de resumen (`resumen_por_temperatura`, solo visual) — son dos evaluaciones independientes del mismo umbral sobre los mismos grupos, no una sola.
- **`TOLERANCIA_ONSET_FRAC = 0.02`**: la deriva de onset se compara contra el 2% del *span* temporal del registro, no contra un valor absoluto en µs — sesiones con registros más largos toleran más deriva absoluta en µs para el mismo 2%.
- **Agrupamiento por escala (`agrupar_por_escala`, clave `(XINCR, YMULT, NR_PT)`)**: determina el universo de comparación tanto del chequeo de mezcla de escalas como del chequeo de deriva de onset y del resumen por temperatura. Dos capturas con distinta `YOFF`/`YZERO`/`PT_OFF` caen en el mismo grupo si coinciden en esos tres campos, y el chequeo de deriva de onset nunca cruza grupos, como ya está anotado en la descripción del propio script (`El Vpp solo es comparable dentro de cada grupo`).
- **Redondeo de temperatura a 1 decimal (`round(c.temperatura, 1)`) en `resumen_por_temperatura`**: agrupa capturas cuyas temperaturas difieren en menos de 0.05 °C como si fueran el mismo punto; esto es solo para el resumen visual, no para los chequeos de `alertas`.
- **Umbral de línea base fijo al primer 5% del registro (`pre = tiempo_us < tiempo_us[0] + 0.05*span`)**: tanto `linea_base` como `ruido_mv` (y por tanto el umbral de onset) dependen de que ese primer 5% sea efectivamente pre-disparo; si el disparo está desplazado y el pulso ya empezó dentro de ese tramo, la línea base y el ruido quedan contaminados y el umbral de onset se calcula sobre una base incorrecta sin que el script lo detecte.

### Constantes o campos declarados sin uso

- Ninguna constante del módulo queda sin usar: `MARGEN_SATURACION`, `UMBRAL_ONSET_SIGMA`, `DISPERSION_ACEPTABLE_PCT` y `TOLERANCIA_ONSET_FRAC` se leen todas dentro de `chequeos()`, `tabla()` o `resumen_por_temperatura()`.
- Campos leídos del CSV pero no usados en ningún chequeo de `chequeos()`: `self.pulsos` (`pulsos_estimados`), `self.eo_delay`, `self.output_level`, `self.timestamp`, `self.modo` (se imprime en `tabla()` pero no participa en ninguna alerta), `self.pico_us` (se imprime en `tabla()` pero no se usa en `chequeos()`).

### Prueba de hipótesis: ¿el umbral relativo de `deriva_onset` puede generar una deriva espuria?

Probada el 2026-09-01 con `generar_sesion_prueba.py` (parámetro `EspecCaptura.ruido_frac`, agregado para esta prueba y conservado en el generador). Hipótesis: que `UMBRAL_ONSET_SIGMA=8.0` (umbral relativo al ruido de cada captura, no absoluto) pudiera hacer que dos capturas con el mismo instante de arribo físico pero niveles de ruido muy distintos produjeran onsets muy distintos, disparando `deriva_onset` sin que exista una deriva real.

**No se reproduce.** Con capturas de la misma escala y el mismo `t_arribo`, variando solo `ruido_frac` (0.001 a 0.058, un ratio de **57x**, muy por encima del ~3x observado entre las series A y B de la sesión 20260825), la deriva de onset resultante fue de **~0.86 µs como máximo** — por debajo del umbral de 1 µs (`TOLERANCIA_ONSET_FRAC=0.02` × 50 µs de span). El corrimiento del cruce de umbral está acotado por el ancho de la envolvente del pulso, no crece indefinidamente con el ruido.

**Limitación de la prueba, no del hallazgo:** el pulso sintético de `_senal_sintetica` es de un solo lóbulo angosto (~0.6 µs de envolvente), muy por debajo de lo que ocuparía una señal fotoacústica real de varios ciclos. La prueba acota el efecto *en el generador*, no lo descarta en datos reales — una señal con más recorrido temporal podría comportarse distinto. (La deriva real observada entre las series A y B de la sesión 20260825, del orden de 30 µs, no la explica este mecanismo de todas formas: el pulso completo dura menos que eso. La explicación más probable es un origen temporal distinto entre series — otro `XZERO`/`PT_OFF`, es decir la posición horizontal del disparo — no el umbral relativo de ruido.)

**Hallazgo colateral, con números concretos:** con amplitud 9 mV y `ruido_frac=0.12` (12%), el umbral de 8σ (8.64 mV) supera el pico real de la señal sintética (4.20 mV), así que `onset_us` queda en `NaN` para esa captura. Basta con que dos capturas de un mismo grupo de escala caigan en ese régimen para que `onsets.size < 3` (línea 195) y el chequeo de deriva se salte entero, en silencio — sin alerta, sin mención en la tabla, sin rastro de que se omitió la comparación. Es un modo de evasión real de `deriva_onset`: ruido suficientemente alto no dispara una alerta de más, apaga la que debería evaluarse.

---

## `simular_barrido.py`

### Tabla de chequeos

| Chequeo (caso) | Qué detecta | Qué deja pasar |
|---|---|---|
| Caso 1 — número de ventanas, orden descendente de objetivos, duraciones ≥ mitad de lo esperado, pulsos>0 | Que el barrido nominal (60→30 °C, paso 5) produzca exactamente 7 ventanas en orden descendente y que ninguna dure menos de la mitad de lo calculado analíticamente vía `duracion_esperada_sim` | Duraciones que excedan de más lo esperado (solo se compara contra la mitad como piso, no hay techo); errores de orden distintos a "no descendente" que aun así pasen la comparación par-a-par; el valor exacto de `pulsos_estimados` (solo se exige `>0`, no que sea razonable) |
| Caso 2 — arranque tibio: objetivos omitidos y ventanas restantes | Que al arrancar con la muestra ya fría se omitan exactamente los objetivos {60,55,50} y que las ventanas restantes sean exactamente [45,40,35,30] | Un desfase de un solo objetivo omitido de más o de menos que aun así deje el resto de la lista con la forma correcta pero desalineada (el chequeo compara la lista completa así que sí lo detectaría; pero no valida el contenido de `temp_apertura`/`temp_cierre` de las ventanas restantes, solo su cantidad y objetivo) |
| Caso 3 — ruido elevado: número de ventanas | Que con `ruido_std=0.3` el filtro de mediana (`VENTANA_MEDIANA`, `CONFIRMACIONES_UMBRAL` en `trigger.py`) siga produciendo 7 ventanas | Que las ventanas se hayan abierto/cerrado en el instante *correcto* pese al ruido — no compara duraciones ni instantes de apertura/cierre contra lo esperado, solo cuenta cuántas ventanas hubo |
| Caso 4 — desconexión breve: orden de advertencias, hubo descarte, ventanas no degeneradas, secuencia terminada | Que "interrumpida" preceda a "restablecida" en el log de advertencias, que se haya emitido algún mensaje de descarte, y que ninguna ventana abra con `temp_apertura` a más de 2.0 °C del objetivo+margen nominal | Ventanas cuya *duración* fue distorsionada por la interrupción (no se compara duración contra lo esperado, solo el punto de apertura); el número exacto de ventanas producidas (ya no se exige un total fijo, según el comentario del propio código) |
| Caso 5 — desconexión permanente: se emite advertencia de fin y la secuencia termina dentro de 30 s reales | Que el mecanismo de aborto por `TIMEOUT_ABORTO_S` (aquí monkeypatcheado a 6 s) efectivamente corte la secuencia y emita el mensaje "Se termina la secuencia" | El comportamiento con los timeouts de producción (`TIMEOUT_ADVERTENCIA_S=30`, `TIMEOUT_ABORTO_S=300`) — el caso reemplaza esas constantes por valores mucho menores solo para esta corrida, así que no ejercita los tiempos reales que usa el sistema en el laboratorio |
| Caso 6 — rebote térmico: no cierra durante el rebote, duración comparable a vecinos | Que un repunte transitorio de temperatura durante la ventana del objetivo 45 no dispare un cierre prematuro, y que la duración resultante quede entre 0.1x y 10x el promedio de los objetivos vecinos (40 y 50) | Un rebote que sí distorsione la duración pero se quede dentro del rango 0.1x–10x (rango muy ancho, deja pasar variaciones grandes); el caso en que no haya vecinos disponibles en `duraciones` (entonces `comparable_ok` queda `False` por `vecinos` vacío, lo cual sí se refleja en el resultado, pero el margen 0.1x-10x en sí es muy laxo) |
| Caso 7 — muestra ya fría: advertencia emitida, cero ventanas, secuencia terminada | Que si la muestra arranca por debajo de todos los objetivos no se abra ninguna ventana y se emita la advertencia correspondiente | — (caso acotado y directo, sin ambigüedad relevante) |
| Caso 8 — objetivo perdido por reconexión durante apertura: advertencia de pérdida, sin ventana degenerada, resto de objetivos correcto | Que un corte que abarca la ventana completa de un objetivo (45) produzca la advertencia de "se perdió" y no genere una ventana espuria para ese objetivo, dejando el resto de la secuencia intacta | Cortes que abarquen *parcialmente* la ventana de apertura pero no toda (zona gris entre "se degrada la ventana" y "se pierde por completo") — ese régimen intermedio no tiene caso propio |
| Nivel 2 — cadena completa: 7 filas en CSV, `pulsos_estimados>0`, temperatura monótona descendente, `Almacenamiento.abrir_sesion` reabre el CSV sin error | Que `Medicion` orqueste correctamente `TriggerWorker` + los dobles de láser/osciloscopio/monitor y que el CSV resultante sea válido para `Almacenamiento.abrir_sesion` | El contenido real de cada fila más allá de `pulsos_estimados` y `temperatura` (no valida `XINCR`/`YMULT`/etc., ni que los `.npy` se hayan guardado correctamente, ni el manejo de `error_flag`); el comportamiento con hardware real (los dobles de láser y osciloscopio siempre devuelven éxito, `DobleOsciloscopio` genera ruido gaussiano puro sin la forma de pulso fotoacústico) |

### Criterios que dependen de umbral, fracción o agrupamiento

- **Caso 1** — `duracion_sim < esperado/2` como criterio de falla: solo detecta ventanas *demasiado cortas*; una ventana anormalmente larga (más del doble de lo esperado) no dispara ninguna alerta.
- **Caso 4** — margen de tolerancia fijo de `2.0` °C entre `temp_apertura` y `objetivo + margen` para considerar la ventana "no degenerada": es un valor absoluto codificado en el caso de prueba, no derivado de `MARGEN_UMBRAL` (0.1) ni de ningún parámetro del sistema real, así que no escala si cambian `POLL_TEMP_S`/`factor_compresion` en otras corridas.
- **Caso 6** — rango `0.1 ≤ duracion/promedio_vecinos ≤ 10.0`: rango de dos órdenes de magnitud; deja pasar rebotes que dupliquen o reduzcan a la mitad la duración de la ventana sin marcarlos como problema.
- **Nivel 2** — `n_filas == 7`: depende de que `t_inicial=60, t_final=30, paso=5` generen exactamente 7 objetivos (`_generar_objetivos` en `trigger.py`); si alguien cambia esos tres parámetros en el caso de prueba sin ajustar el `7`, el chequeo fallaría por una razón ajena al comportamiento real del sistema.
- **`RESOLUCION_SENSOR = 0.0625`** determina la cuantización de todas las lecturas simuladas de temperatura; junto con `VENTANA_MEDIANA=3` y `CONFIRMACIONES_UMBRAL=2` de `trigger.py` (no definidos aquí, solo consumidos indirectamente vía `TriggerWorker`), fija cuánta demora de confirmación introduce el filtro real frente a la curva simulada — el simulador no verifica ese acoplamiento por separado, solo el resultado agregado de cada caso.
- **Agrupamiento por objetivo** (`ColectorSenales._pendientes`, `colector.ventanas`): todos los chequeos de casos 1-8 dependen de que la correspondencia entre advertencias de texto (parseadas con expresiones regulares) y la cola `_pendientes` se mantenga sincronizada; un cambio en el formato de los mensajes de `trigger.py` (p. ej. reordenar "Se omiten los objetivos..." vs "Se omiten además...") rompe el parseo sin que quede claro si el fallo es del simulador o de un cambio real de comportamiento.

### Constantes o campos declarados sin uso

- `RESOLUCION_SENSOR` se usa (cuantiza la lectura simulada).
- `T0`, `T_amb`, `tau`, `factor` se reasignan como variables locales en cada función `caso_N`, no como constantes de módulo; todas se usan dentro de su función.
- `ColectorSenales.objetivos_totales` se asigna en `__init__` pero **no se lee en ningún caso** (ni en la tabla `imprimir_tabla`, ni en ningún chequeo de los 8 casos ni del nivel 2).
- `duracion_real_total_estimada()` está definida y se usa solo desde el Caso 4 (`total_real = duracion_real_total_estimada(...)`); no se usa en el Caso 8 pese a resolver un problema similar (ahí el cálculo de ventana de corte se hace directamente con `t_sim_para_temp` in-line).
- El parámetro `semilla` de `DobleTemperaturaSimulada` se pasa siempre en los 8 casos, pero nunca se usa para verificar reproducibilidad entre corridas (no hay ningún caso que compare dos ejecuciones con la misma semilla y confirme que produzcan el mismo resultado); el docstring lo justifica solo como aislamiento del ruido frente a lecturas extra del colector, no como mecanismo de repetibilidad verificado.

---

## `generar_sesion_prueba.py` + `probar_verificacion.py`

Refactorizado el 2026-09-01: el generador pasó de producir solo dos variantes de
formato (sin ninguna condición de alerta) a producir, además, una sesión sintética
por cada condición de alerta de `verificar_sesion.py`, a partir de una lista de
`EspecCaptura` (una especificación por captura: escala, temperatura, amplitud,
semilla, `error_flag`, si escribe `.npy`, de qué otra captura reutiliza el `raw`).
`probar_verificacion.py` genera cada caso, corre `verificar_sesion.py` sobre él y
exige que dispare exactamente el conjunto de alertas esperado — ni de más, ni de
menos — con el código de salida correcto.

### Tabla de chequeos

Ninguno de los dos scripts valida nada de forma independiente de
`verificar_sesion.py`: `generar_sesion_prueba.py` genera datos sintéticos y
`probar_verificacion.py` delega el veredicto de cada caso a la propia salida de
`verificar_sesion.py`. Se documenta igual qué produce y qué no, y qué tan
ajustado (frágil) es cada caso al comportamiento actual del verificador.

| Aspecto generado | Qué cubre | Qué deja pasar |
|---|---|---|
| Dos formatos de header (`HEADER_NUEVO`, `HEADER_VIEJO`), sin cambios en este refactor | Compatibilidad hacia atrás de lectores de CSV (columnas de láser presentes/ausentes) | Ningún otro formato de header histórico intermedio; no genera un CSV con columnas *corridas* o faltantes a mitad de archivo, ni con tipos de dato inválidos en una celda |
| `metadatos_sesion.txt` (`_escribir_metadatos`, reimplementada en el generador) | Que cada sesión sintética tenga el mismo archivo que produce el sistema real al crear una sesión, con el mismo formato de dos líneas mínimas (`session_id`, `fecha_creacion`) | El generador siempre llama con `metadatos={}` — no inventa identidad de instrumental (puerto láser, host del osciloscopio, etc.) porque no tiene hardware real que describir; ver más abajo "Dos productores de `metadatos_sesion.txt`" para el riesgo de que ambas copias del formato diverjan |
| Señal sintética (`_senal_sintetica`), ahora parametrizable en `n_puntos`, `xincr`, `ymult` y `t_arribo` por captura, con recorte a rango de `int16` | Forma general de pulso fotoacústico con ruido gaussiano proporcional a la amplitud; el recorte permite simular saturación real de ADC sin desbordar el tipo | El recorte es simétrico y duro (satura de golpe al llegar al límite); no modela un recorte suave ni asimetría de ADC unsigned; sigue siendo un solo lóbulo, no una oscilación amortiguada de varios ciclos (ver docstring de `_senal_sintetica`) |
| 10 casos de `CASOS_VERIFICACION` (uno por alerta) + `limpio` | Cada condición de alerta enumerada en la tabla de `verificar_sesion.py` de más arriba, aislada en su propio caso (salvo los dos acoplamientos documentados abajo); `limpio` no dispara ninguna | Combinaciones de dos o más alertas *no acopladas* disparándose a la vez en una sola sesión (cada caso dispara como máximo el conjunto documentado en `ALERTAS_ESPERADAS`); no hay caso que ejercite una alerta al límite exacto del umbral (todos apuntan claramente por encima o por debajo, no a la frontera) |
| `error_flag` sintético | Un caso dedicado (`error_flag`) con exactamente una captura marcada `error_flag=1`; el resto de los casos (incluido `limpio`) no marcan ninguna | A diferencia de antes del refactor, ya no hay una fila con `error_flag=1` "de regalo" en cada sesión generada — pero tampoco se generan sesiones con más de una captura marcada, ni con `error_desc` distinto de `"temperatura no detectada"` |
| Temperaturas explícitas por captura (campo `temperatura` de cada `EspecCaptura`) | Tanto secuencias estrictamente descendentes (`limpio`, la mayoría de los casos) como el caso deliberado de temperatura idéntica en todas las capturas (`temperatura_estancada`) | No genera temperaturas no monótonas fuera de ese caso dedicado, ni huecos, ni el escenario de "congelamiento parcial" (dos bloques de temperatura constante distintos) que la sección de `verificar_sesion.py` ya señala como fuera de alcance del propio verificador |
| `probar_verificacion.py`: comparación por subcadena (`FIRMA_ALERTA`) contra el texto impreso por `verificar_sesion.py` | Que la alerta esperada aparezca y que ninguna alerta fuera del conjunto esperado aparezca, vía búsqueda de subcadena en `stdout+stderr` | Depende del texto literal de cada mensaje de alerta: si `verificar_sesion.py` reformula un mensaje sin cambiar su condición, el runner puede reportar un falso "faltante" o "inesperada" sin que el comportamiento real haya cambiado; no valida el contenido de la tabla ni del resumen por temperatura, solo la sección `ALERTAS` |

### Dos productores de `metadatos_sesion.txt`

`Almacenamiento._escribir_metadatos` ([app/almacenamiento/almacenamiento.py:138-157](../app/almacenamiento/almacenamiento.py)) y `generar_sesion_prueba._escribir_metadatos` son dos implementaciones independientes del mismo formato de archivo, no una reutilización de la misma función. Se evaluó reutilizar la real y se descartó por dos razones concretas, no por preferencia de estilo:

- **Usar `Almacenamiento.nueva_sesion()` completo** habría chocado con el diseño ya construido en este refactor: `nueva_sesion()` escribe su propio encabezado de CSV (fijo, vía `CSV_HEADER` — sin equivalente a `HEADER_VIEJO`) y cualquier fila tendría que pasar por `guardar()`, que arma el nombre del `.npy` y el `medicion_id` a partir de un contador interno (`_medicion_idx`) autoincremental — incompatible con el esquema por `EspecCaptura.etiqueta` y con `raw_de`/`escribir_npy` que ya sostienen los 11 casos de verificación.
- **Usar solo `_escribir_metadatos()` suelto** evita ese choque, pero es un método de instancia que lee `self._sesion_dir` y `self._session_id` — atributos privados fijados por `__init__`/`nueva_sesion()`. Llamarlo habría exigido instanciar un `Almacenamiento` (un `QObject` de PySide6, con señales) y asignarle esos dos atributos privados a mano, solo para escribir un `.txt` de dos líneas: más acoplamiento (a Qt y a los internos de la clase) que el que evita.

Por eso se reimplementó por separado. Consecuencia: si `_escribir_metadatos` cambia de formato en `app/almacenamiento/almacenamiento.py` (nuevas líneas, otro orden, otro separador), la copia del generador no se entera y las sesiones sintéticas quedan con un `metadatos_sesion.txt` desactualizado sin que nada lo señale — ningún chequeo de `verificar_sesion.py` ni de `probar_verificacion.py` lee ese archivo, así que una divergencia aquí es silenciosa.

### Desfase entre diseño y código: `metadatos_sesion.txt` no está en el diagrama

`Tesis documento\Diagramas\modulo_almacenamiento.mermaid`, nodo `NEW` (línea 9): *"Genera session_id único / Crea carpeta sesión / Crea CSV con encabezados"* — no menciona escribir `metadatos_sesion.txt`, aunque `nueva_sesion()` lo hace inmediatamente después de crear el CSV ([almacenamiento.py:134](../app/almacenamiento/almacenamiento.py)). No es un bug: el código hace más de lo que el diagrama documenta, no al revés. Pero es diseño y código divergiendo sin que quede registro, y ahora el generador de pruebas replica ese comportamiento no documentado — si alguien redibuja el diagrama a partir de sí mismo (en vez de del código), esta escritura se perdería otra vez.

### Acoplamientos entre condiciones (no se pueden aislar)

- `buffer_repetido_escala_distinta` ↔ `mezcla_escalas`: reusar el mismo `raw` con una escala distinta crea, por construcción, dos grupos de escala — dispara ambas alertas siempre. Documentado como comentario de una línea en `caso_buffer_repetido_escala_distinta()` y reflejado en `ALERTAS_ESPERADAS`.
- `sin_reconstruibles` ↔ `npy_ausente`: en `verificar_sesion.py`, la lista `faltantes` (línea ~149) se llena con las mismas etiquetas *antes* del `return` temprano que agrega "sin captura reconstruible" (línea ~153) — toda sesión sin ninguna captura reconstruible por `.npy` ausente dispara ambas alertas. También documentado como comentario de una línea en `caso_sin_reconstruibles()` y reflejado en `ALERTAS_ESPERADAS`.

### Criterios que dependen de umbral, fracción o agrupamiento

- Los casos que apuntan a un umbral de `verificar_sesion.py` (`saturacion` contra `MARGEN_SATURACION`, `deriva_onset` contra `TOLERANCIA_ONSET_FRAC`, `dispersion_vpp` contra `DISPERSION_ACEPTABLE_PCT`) usan valores muy por encima o muy por debajo del umbral, no al límite — si `verificar_sesion.py` cambia el valor del umbral dentro de un rango razonable, estos casos casi seguro lo siguen ejercitando igual; no serían el primero en detectar un cambio de umbral sutil.
- `caso_deriva_onset` depende de que el registro sea lo bastante largo (`N_PUNTOS=25000`, `XINCR=2e-9` → 50 µs) para que un desplazamiento del arribo de ±1–1.2 µs siga cayendo dentro de la ventana temporal; si `N_PUNTOS` o `XINCR` se reducen sin ajustar los desplazamientos del caso, la señal podría quedar recortada antes de completar el pulso.
- `caso_temperatura_estancada` y `caso_dispersion_vpp` controlan la dispersión de Vpp *dentro* de cada grupo de temperatura+escala con amplitudes deliberadamente cercanas (para no disparar `dispersion_vpp` donde no corresponde) o deliberadamente separadas (para dispararla donde sí corresponde) — son valores elegidos a mano contra `DISPERSION_ACEPTABLE_PCT=5.0`, no derivados de él; si el umbral cambia, hay que revisar ambos casos a mano.

### Constantes o campos declarados sin uso

- `T_ARRIBO_S`, `ANCHO_S`, `FRECUENCIA_HZ`, `N_PUNTOS`, `XINCR`, `YMULT` se usan todas, como valores por defecto de `EspecCaptura` y dentro de `_senal_sintetica`.
- Header `HEADER_VIEJO` sigue sin incluir `output_level`, `eo_delay_us`, `burst_mode`; `_fila()` las sigue descartando silenciosamente vía `valores.get(col, "")` filtrando por el header — mismo comportamiento que antes del refactor, ahora además compartido por los 10+1 casos nuevos (todos usan `HEADER_NUEVO`, así que en la práctica el formato viejo solo lo ejercitan `generar_variante`/`nuevo`/`viejo`).
- `EspecCaptura.modo`, `.output_level`, `.eo_delay_us`, `.burst_mode` se escriben al CSV en todos los casos pero ningún caso los varía respecto a su valor por defecto — no hay ninguna condición de alerta de `verificar_sesion.py` que dependa de ellos (confirmado en el inventario: `self.eo_delay`, `self.output_level` y `self.modo` se leen en `Captura.__init__` pero no participan en `chequeos()`).
- `CH_SCALE` está clavado en `0.005` dentro de `generar()` (no es campo de `EspecCaptura`); ningún caso, ni la variante `nuevo`/`viejo`, puede variarlo — a diferencia de `HOR_SCALE`, que sí se deriva por captura (`spec.xincr * spec.nr_pt / 10`).

### Prueba de mutación sobre `ALERTAS_ESPERADAS`

Corrida el 2026-09-01 contra el commit `c8af5ba37bdd833f0acec5579984b83a7847d3cb`, para descartar que `ALERTAS_ESPERADAS` (en `probar_verificacion.py`) se hubiera derivado de la salida observada de `verificar_sesion.py` en vez del comportamiento pretendido — si fuera así, el corredor pasaría igual con el verificador roto y el 11/11 no valdría nada.

Método: por cada una de las 10 condiciones de `chequeos()`, se comentó únicamente el `alertas.append(...)` de esa condición (dejando intacta cualquier estructura de control que otras condiciones necesiten, p. ej. el `return` de `sin_reconstruibles`), se corrió `probar_verificacion.py`, se anotó qué casos cayeron, y se restauró el archivo con `git checkout -- herramientas/verificar_sesion.py` antes de mutar la siguiente condición.

| Chequeo desactivado | Casos que fallaron |
|---|---|
| `npy_ausente` (líneas 149-151) | `npy_ausente`, `sin_reconstruibles` |
| `sin_reconstruibles` (línea 154) | `sin_reconstruibles` |
| `saturacion` (líneas 157-163) | `saturacion` |
| `buffer_repetido_misma_escala` (líneas 174-179) | `buffer_repetido_misma_escala` |
| `buffer_repetido_escala_distinta` (líneas 167-173) | `buffer_repetido_escala_distinta` |
| `mezcla_escalas` (líneas 182-190) | `mezcla_escalas`, `buffer_repetido_escala_distinta` |
| `deriva_onset` (líneas 199-205) | `deriva_onset` |
| `temperatura_estancada` (líneas 208-212) | `temperatura_estancada` |
| `error_flag` (líneas 214-216) | `error_flag` |
| `dispersion_vpp` (líneas 223-228) | `dispersion_vpp` |

Las 10 mutaciones matan al menos el caso correspondiente; ninguna dejó los 11 casos en verde.

Dos observaciones:

- **La mutación de `sin_reconstruibles` deja el código de salida en 1.** Sin la comparación de conjuntos de `probar_verificacion.py` (que exige que la alerta esperada aparezca, no solo que el exit code sea el correcto), ese caso habría pasado en verde con el chequeo desactivado: el `return alertas` temprano de la línea 154 sigue devolviendo una lista no vacía porque `npy_ausente` (línea 151) ya puso algo ahí antes. Un corredor que solo comparara `exit_code` contra 0/1 no habría detectado esta mutación.
- **Las dos filas con dos casos caídos reproducen exactamente los acoplamientos ya documentados por análisis del código** en "Acoplamientos entre condiciones" más arriba (`buffer_repetido_escala_distinta` ↔ `mezcla_escalas`, `npy_ausente` ↔ `sin_reconstruibles`) — no son un hallazgo nuevo de la mutación, son la misma estructura ya prevista, confirmada empíricamente.

**Esta prueba fue manual y puntual, no automatizada ni repetible.** No hay un script de mutación en el repositorio; los 10 cambios se aplicaron y revirtieron a mano sobre `verificar_sesion.py` en una sesión de trabajo. Si `verificar_sesion.py` cambia (nueva condición, chequeo reescrito, líneas movidas), esta tabla queda desactualizada y hay que volver a correr la mutación a mano — nada la vuelve a ejecutar automáticamente.

---

## `probar_advertencias_gui.py`

### Tabla de chequeos

| Chequeo | Qué detecta | Qué deja pasar |
|---|---|---|
| `len(ventana._advertencias) == len(MENSAJES)` | Que cada `advertencia.emit(...)` de `Medicion` termine acumulada en `VentanaAmbos._advertencias` | Que el contenido de cada entrada acumulada sea el mensaje correcto en el orden correcto (solo compara la cuenta, no el contenido ni el orden) |
| `str(len(MENSAJES)) in progreso` | Que el contador de advertencias aparezca en el texto de `_lbl_progreso` | Falsos positivos si el número de mensajes (4) aparece en el texto por otra razón (p. ej. como parte de otro número); no verifica el resto del contenido del texto |
| `"d29922" in ventana._lbl_progreso.styleSheet()` | Que la hoja de estilo de la etiqueta de progreso contenga el color ámbar esperado tras una advertencia | Que el color se aplique en el momento correcto (solo se verifica el estado final, no que cada `_on_advertencia` individual lo haya aplicado); un cambio de paleta que use el mismo tono en otro contexto no relacionado pasaría igual |
| Existencia y longitud de `advertencias.log` | Que el archivo se cree y tenga tantas líneas no vacías como mensajes emitidos | Contenido exacto de cada línea (solo se compara conteo); el log se **reescribe completo** en cada emisión (`write_text`, no *append*) vía `_registrar_advertencias_en_sesion` — el chequeo de longitud final no puede distinguir esto de un log que hiciera *append* correctamente, ambos llegan al mismo estado final |
| Primera línea del log empieza con `"["` | Que las líneas lleven marca de tiempo (formato `[HH:MM:SS] mensaje`) | Que la marca de tiempo sea correcta o consistente con el momento real de cada advertencia (no se parsea la hora, solo se comprueba el primer carácter) |
| Apertura manual del diálogo de fin de secuencia (`_on_secuencia_ok`) | Nada de forma automática — el script imprime una instrucción para que un humano revise visualmente que el botón de detalles muestre las cuatro advertencias | Este paso queda completamente fuera de la verificación automática (`codigo` ya se calculó antes de abrir el diálogo); un fallo visual en el diálogo no afecta el código de salida del script |

### Criterios que dependen de umbral, fracción o agrupamiento

- No hay umbrales numéricos ni fracciones: todos los chequeos son de igualdad exacta de conteos o de pertenencia de subcadena (`in`).
- El único "agrupamiento" implícito es que las cuatro `MENSAJES` se emiten en una sola sesión temporal sin reiniciar `_advertencias` entre medio — el script no cubre el caso de que `Medicion.advertencia` se emita durante más de una sesión, ni el comportamiento de `_advertencias.clear()` que ocurre normalmente al iniciar una nueva secuencia (línea 1106 de `ventana_ambos.py`).

### Constantes o campos declarados sin uso

- `MENSAJES` se usa completo (emitido y contado).
- El valor de retorno de `verificar()` (`codigo`) se calcula antes de que el usuario revise el diálogo manual; el resultado del script por tanto **no depende en absoluto** de la única parte no automatizada (el diálogo de fin de secuencia), aunque el script se lo pide al usuario como paso explícito.
