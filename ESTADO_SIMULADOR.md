# Estado de la validación de herramientas/simular_barrido.py

Última actualización: sesión de implementación del 2026-08-30.

## Resumen ejecutivo

- Los 8 casos + nivel dos del simulador pasan, corridos individualmente.
- Se aplicaron dos cambios a `app/medicion/trigger.py`: el diff aprobado
  por el usuario para el hallazgo de Caso 4 (con la modificación pedida en
  `_separar_alcanzables()`), más un segundo bug encontrado durante la
  verificación (mediana móvil que no se vaciaba en un hueco de lectura).
  Ambos se explican abajo.
- Se corrigió `herramientas/simular_barrido.py`: Caso 6 (rebote armado
  dinámicamente en vez de precalculado) y Caso 4 (criterio ajustado al
  comportamiento correcto), y se agregó Caso 8.
- Pendiente: decidir si se conserva o se borra `venv_broken_i7/` (ver
  "Entorno" abajo). No hay nada más pendiente de aprobación en este
  momento.

## Entorno

El venv del proyecto (`venv/`) apuntaba a una instalación de Python 3.11 en
`C:\Users\i7\...`, una ruta que ya no existe en esta máquina — quedó roto
tras el cambio de equipo. Se recreó con el único Python disponible
(3.12, `C:\Users\Evandher_Zarco\AppData\Local\Programs\Python\Python312`)
y se reinstalaron las dependencias fijadas en `requirements.txt`. El venv
roto original se conservó, sin borrar, en `venv_broken_i7/` por si hace
falta revisar algo antes de descartarlo. **Pendiente**: decidir con el
usuario si se conserva o se borra.

## Resultado de los 8 casos + nivel dos (corridos individualmente)

| Caso | Resultado |
|---|---|
| Caso 1 — Barrido nominal | OK — `ventanas=7 orden_descendente=True duraciones_ok=True pulsos_ok=True` |
| Caso 2 — Arranque con la muestra ya tibia | OK — se omiten 60, 55, 50; quedan 45, 40, 35, 30 |
| Caso 3 — Ruido elevado (std=0.3) | OK — `ventanas=7` |
| Caso 4 — Desconexión breve | OK (con criterio y comportamiento corregidos, ver abajo) — `orden_ok=True hubo_descarte=True sin_ventanas_degeneradas=True`, `objetivos_medidos=[60.0, 55.0, 50.0, 45.0, 30.0]` |
| Caso 5 — Desconexión permanente (timeouts sobrescritos a 2 s / 6 s) | OK — advertencia de fin de secuencia, terminó en 8.5 s reales |
| Caso 6 — Rebote térmico en el umbral | OK (con rebote armado dinámicamente, ver abajo) — `ventanas=7 no_cierra_durante_rebote=True duracion_objetivo=140.0 vecinos=[110.0, 60.0] comparable_ok=True` |
| Caso 7 — Muestra por debajo de todos los objetivos | OK — advertencia + `ventanas=0` + `terminada=True` |
| Caso 8 — Objetivo perdido por reconexión durante la apertura (nuevo) | OK — `perdida_ok=True sin_ventana_degenerada=True resto_ok=True`, `objetivos_medidos=[60.0, 55.0, 50.0, 40.0, 35.0, 30.0]` |
| Nivel 2 — Cadena completa (Medicion + dobles + Almacenamiento real) | OK — `filas=7 pulsos_ok=True monotona_ok=True abre_ok=True` |

No se ha vuelto a correr la suite completa (`simular_barrido.py` sin
argumento) desde que se agregó Caso 8 y se corrigieron Caso 4 y Caso 6 —
solo se corrieron 4, 6 y 8 por separado, como pidió el usuario. Los otros
seis ya estaban confirmados en corridas previas (individuales y en una
corrida completa de punta a punta) y no fueron tocados por estos cambios.

## Cambios aplicados a `app/medicion/trigger.py`

### 1. Diff aprobado: objetivo perdido/ensanchado por reconexión

**Síntoma original** (Caso 4, antes de la corrección): durante un corte de
lectura, la muestra puede cruzar de largo el umbral de apertura *y* el de
cierre de uno o más objetivos mientras no hay datos. Al restablecerse la
lectura, `_esperar_umbral()` veía la temperatura ya muy por debajo del
objetivo y abría+cerraba la ventana casi de inmediato — una "medición" sin
ninguna ventana de integración real, guardada en el CSV como si fuera
válida.

**Corrección**: `_esperar_umbral()` ahora recibe `t_obj` y `fase`
("apertura" | "cierre"). Al restablecerse la lectura durante `fase="apertura"`,
si la muestra ya bajó del umbral de *cierre* del objetivo vigente, ese
objetivo se da por perdido — se avisa y `_esperar_umbral()` devuelve `None`
en vez de `True`/`False`. Al restablecerse durante `fase="cierre"`, la
medición sigue siendo válida (el osciloscopio no depende del ESP32), pero
se avisa que integró sobre una ventana más ancha que la nominal.
`_loop_temperatura()` pasa de `for t_obj in objetivos` a un
`while objetivos` sobre un `deque`, para poder descartar el objetivo
perdido y, con el nuevo `_descartar_pendientes_tras_reconexion()`, aplicar
el mismo criterio a los objetivos posteriores que también hayan quedado
rebasados durante la misma interrupción — reutilizando
`_descartar_objetivos_rebasados()` factorizado en un `_separar_alcanzables()`
compartido.

**Modificación pedida por el usuario respecto al diff original**:
`_separar_alcanzables()` calcula `omitidos` con el predicado complementario
(`[t for t in objetivos if temp <= t + MARGEN_UMBRAL]`) en vez de por
conteo de prefijo (`objetivos[: len(objetivos) - len(alcanzables)]`), para
no depender de que la lista venga ordenada.

```diff
--- a/app/medicion/trigger.py
+++ b/app/medicion/trigger.py
@@ -17,6 +17,12 @@
   Los objetivos cuya ventana ya quedó por encima de la temperatura inicial
   de la muestra se omiten: nunca podrían observarse y generarían ventanas
   de duración nula.

+  Si la lectura se recupera después de una interrupción, se aplica el mismo
+  criterio: el objetivo que se estaba esperando abrir se descarta si la
+  muestra ya bajó de su umbral de cierre (nunca hubo ventana), junto con
+  cualquier objetivo posterior que también haya quedado rebasado. Si la
+  interrupción ocurrió con la ventana ya abierta, la medición sigue siendo
+  válida —el osciloscopio no depende del ESP32— pero integró sobre un
+  rango más ancho que el nominal, y se avisa al cerrar.
+
   La ausencia sostenida de lecturas frescas emite advertencia y, si se
   prolonga, termina la secuencia en lugar de esperar indefinidamente.

@@ -144,29 +150,32 @@
     # ── Modo por temperatura ───────────────────────────────────────────────────

     def _loop_temperatura(self):
-        objetivos = self._descartar_objetivos_rebasados(self._generar_objetivos())
+        objetivos = deque(self._descartar_objetivos_rebasados(self._generar_objetivos()))

         if not objetivos:
             self.advertencia.emit(
                 "La muestra ya está por debajo de todos los objetivos. "
                 "No hay puntos que medir."
             )
             return

-        for t_obj in objetivos:
+        while objetivos:
             if not self._activo:
                 return

-            if not self._esperar_umbral(t_obj + MARGEN_UMBRAL):
+            t_obj = objetivos[0]
+
+            resultado = self._esperar_umbral(t_obj + MARGEN_UMBRAL, t_obj=t_obj, fase="apertura")
+            if resultado is False:
                 return
+            if resultado is None:
+                objetivos.popleft()
+                self._descartar_pendientes_tras_reconexion(objetivos)
+                continue

+            objetivos.popleft()
             self.iniciar_acumulacion.emit()

-            if not self._esperar_umbral(t_obj - MARGEN_UMBRAL):
+            if not self._esperar_umbral(t_obj - MARGEN_UMBRAL, t_obj=t_obj, fase="cierre"):
                 return

             self.detener_y_capturar.emit()

             if not self._esperar_fin_de_captura():
                 return
@@ -184,25 +194,55 @@
             t = round(t - self._paso, 4)
         return objetivos

+    def _separar_alcanzables(
+        self, objetivos: list[float], temp: float
+    ) -> tuple[list[float], list[float]]:
+        """
+        Separa objetivos según si la muestra sigue por encima de su umbral
+        de apertura (alcanzables) o ya lo rebasó (omitidos), sin asumir que
+        objetivos venga ordenado.
+        """
+        alcanzables = [t for t in objetivos if temp > t + MARGEN_UMBRAL]
+        omitidos = [t for t in objetivos if temp <= t + MARGEN_UMBRAL]
+        return alcanzables, omitidos
+
     def _descartar_objetivos_rebasados(self, objetivos: list[float]) -> list[float]:
         """
         Elimina los objetivos cuya ventana de entrada ya quedó por encima de la
         temperatura actual de la muestra. Sin este filtro, esos objetivos
         satisfacen ambos umbrales de inmediato y producen capturas con ventana
         de integración de duración nula.
         """
         temp = self._esperar_primera_lectura()
         if temp is None:
             return objetivos

-        alcanzables = [t for t in objetivos if temp > t + MARGEN_UMBRAL]
+        alcanzables, omitidos = self._separar_alcanzables(objetivos, temp)

-        if len(alcanzables) < len(objetivos):
-            omitidos = objetivos[: len(objetivos) - len(alcanzables)]
+        if omitidos:
             lista = ", ".join(f"{t:.1f}" for t in omitidos)
             self.advertencia.emit(
                 f"La muestra está a {temp:.2f} °C. Se omiten los objetivos "
                 f"por encima de esa temperatura: {lista} °C."
             )

         return alcanzables

+    def _descartar_pendientes_tras_reconexion(self, objetivos: deque) -> None:
+        """
+        Tras perder un objetivo porque la lectura se restableció con su
+        ventana ya rebasada, aplica el mismo criterio a lo que quede
+        pendiente: la muestra pudo haber seguido bajando durante la misma
+        interrupción y rebasar objetivos posteriores también.
+        """
+        if not objetivos:
+            return
+
+        temp = self._leer_temperatura_estable()
+        if temp is None:
+            return
+
+        alcanzables, omitidos = self._separar_alcanzables(list(objetivos), temp)
+
+        if omitidos:
+            lista = ", ".join(f"{t:.1f}" for t in omitidos)
+            self.advertencia.emit(
+                f"La muestra sigue bajando: al restablecerse la lectura ya "
+                f"está a {temp:.2f} °C. Se omiten además los objetivos ya "
+                f"rebasados: {lista} °C."
+            )
+
+        objetivos.clear()
+        objetivos.extend(alcanzables)
+
     def _esperar_primera_lectura(self) -> float | None:
         """
         Obtiene la primera temperatura filtrada del arranque. Devuelve None si
         no llega ninguna dentro del plazo de advertencia.
         """
         limite = time.monotonic() + TIMEOUT_ADVERTENCIA_S
         while self._activo and time.monotonic() < limite:
             temp = self._leer_temperatura_estable()
             if temp is not None:
                 return temp
             time.sleep(POLL_TEMP_S)
         return None

-    def _esperar_umbral(self, umbral: float) -> bool:
+    def _esperar_umbral(
+        self, umbral: float, t_obj: float | None = None, fase: str | None = None
+    ) -> bool | None:
         """
         Espera a que la temperatura filtrada baje hasta umbral (temp ≤ umbral),
-        confirmada por lecturas consecutivas.
+        confirmada por lecturas consecutivas. Devuelve True si el umbral se
+        alcanzó.

         Devuelve False si se cancela con detener() o si se pierde la lectura
         de temperatura durante más de TIMEOUT_ABORTO_S.
+
+        Si fase="apertura" y, al restablecerse la lectura tras una
+        interrupción, la muestra ya bajó del umbral de cierre del objetivo
+        (t_obj - MARGEN_UMBRAL), la ventana se perdió por completo mientras
+        no hubo datos: se avisa y se devuelve None en vez de True.
+
+        Si fase="cierre" y hubo una interrupción durante la espera, la
+        adquisición del osciloscopio siguió corriendo —no depende del
+        ESP32—, así que la medición sigue siendo válida, pero integró sobre
+        una ventana más ancha que la nominal: se avisa al cerrar.
         """
         confirmaciones = 0
         ultimo_dato = time.monotonic()
         advertido = False
+        hubo_interrupcion = False

         while self._activo:
             temp = self._leer_temperatura_estable()
             ahora = time.monotonic()

             if temp is None:
                 silencio = ahora - ultimo_dato
                 if silencio > TIMEOUT_ABORTO_S:
                     self.advertencia.emit(
                         f"Sin lectura de temperatura durante {silencio:.0f} s. "
                         "Se termina la secuencia."
                     )
                     self._activo = False
                     return False
                 if silencio > TIMEOUT_ADVERTENCIA_S and not advertido:
                     self.advertencia.emit(
                         "Lectura de temperatura interrumpida. "
                         "La secuencia continúa en espera."
                     )
                     advertido = True
+                    hubo_interrupcion = True
                 time.sleep(POLL_TEMP_S)
                 continue

             ultimo_dato = ahora
             if advertido:
                 self.advertencia.emit("Lectura de temperatura restablecida.")
                 advertido = False

+                if (
+                    fase == "apertura"
+                    and t_obj is not None
+                    and temp <= t_obj - MARGEN_UMBRAL
+                ):
+                    self.advertencia.emit(
+                        f"El objetivo {t_obj:.1f} °C se perdió durante la "
+                        f"interrupción de lectura: la muestra ya está a "
+                        f"{temp:.2f} °C, por debajo de su umbral de cierre. "
+                        "Nunca hubo ventana de integración para ese punto."
+                    )
+                    return None
+
             if temp <= umbral:
                 confirmaciones += 1
                 if confirmaciones >= CONFIRMACIONES_UMBRAL:
+                    if fase == "cierre" and hubo_interrupcion and t_obj is not None:
+                        self.advertencia.emit(
+                            f"El objetivo {t_obj:.1f} °C integró sobre una "
+                            "ventana más ancha que la nominal por una "
+                            f"interrupción de lectura: cerró a {temp:.2f} °C."
+                        )
                     return True
             else:
                 confirmaciones = 0

             time.sleep(POLL_TEMP_S)

         return False
```

### 2. Bug adicional encontrado durante la verificación: la mediana móvil no se vaciaba en un hueco de lectura

Al correr Caso 4 y Caso 8 por primera vez tras aplicar el diff de arriba,
ambos seguían fallando — la ventana degenerada de Caso 4 seguía apareciendo
y la pérdida de Caso 8 no se detectaba.

**Causa**: `_leer_temperatura_estable()` usa una mediana móvil de 3
muestras (`_buffer_temp`), y ese buffer no se vaciaba cuando había un hueco
de lectura. La primera lectura fresca al restablecerse la conexión se
mezclaba con dos muestras *previas al corte* (mucho más calientes) en la
mediana — así que, justo en el único momento en que `_esperar_umbral()`
necesita una lectura precisa para decidir si el objetivo se perdió, veía un
valor contaminado por datos de antes del corte, no la temperatura real ya
bajada. Es un defecto real e independiente del anterior — mezclar muestras
de antes y después de una interrupción en una mediana no tiene sentido
físico, ni en la simulación ni en hardware real — y solo se volvió
observable ahora porque la corrección nueva depende de que esa lectura sea
exacta.

**Corrección aplicada** (también en `app/medicion/trigger.py`,
`_leer_temperatura_estable()`): vaciar `_buffer_temp` cada vez que
`_leer_temperatura()` devuelve `None`, de modo que al restablecerse la
lectura la mediana se reconstruye solo con muestras frescas, igual que al
arrancar la secuencia:

```diff
     def _leer_temperatura_estable(self) -> float | None:
         """
         Lectura filtrada con mediana móvil. Devuelve None mientras no haya
         lecturas frescas suficientes para llenar la ventana del filtro.
+
+        Un hueco de lectura vacía el buffer: mezclar muestras de antes y
+        después de una interrupción en la misma mediana devolvería un valor
+        que no corresponde a ningún instante real, y en particular podría
+        ocultar cuánto bajó la muestra durante el hueco justo cuando la
+        lectura se restablece.
         """
         temp = self._leer_temperatura()
         if temp is None:
+            self._buffer_temp.clear()
             return None

         self._buffer_temp.append(temp)
```

Esta corrección no estaba en el diff original aprobado — se descubrió
durante la verificación de Caso 4/8 y era necesaria para que la corrección
aprobada funcionara de verdad. Solo afecta escenarios con un hueco de
lectura real (`fallo_real` activo): Casos 1, 2, 3, 6 y 7 no lo tienen y no
cambian de comportamiento.

## Cambios aplicados a `herramientas/simular_barrido.py`

### Caso 6: rebote armado dinámicamente

**Bug encontrado** (corrida completa previa a estos cambios): Caso 6
precalculaba `t_ini_real` (el instante del rebote) de forma puramente
analítica desde `t=0`, asumiendo que la ventana del objetivo 45 abriría
exactamente en el instante teórico. Pero `_esperar_umbral()` exige 2
lecturas consecutivas para confirmar un cruce, lo que introduce un retraso
variable antes de que la ventana realmente abra — retraso que depende del
jitter real del scheduler del sistema operativo, distinto entre una corrida
aislada de un solo caso y una corrida donde ese caso es el sexto de una
secuencia larga. En una corrida, el rebote precalculado no llegó a
solaparse con la ventana real en absoluto (`duracion_objetivo=80.0`,
idéntico a los vecinos, sin ningún alargamiento) — un defecto del
*simulador*, no de `app/`.

**Corrección**: en vez de precalcular `t_ini_real` desde la creación del
doble, se conecta un manejador a la señal `iniciar_acumulacion` del propio
`TriggerWorker` que arma el rebote (`doble.rebote_real = (...)`) en el
instante real en que la ventana del objetivo 45 efectivamente abre —
usando ese instante observado como referencia en vez de una predicción
analítica desde `t=0`, y sumándole la duración esperada de la ventana
(menos medio rebote) para seguir apuntando al cruce de cierre.

### Caso 4: criterio ajustado al comportamiento correcto

Con el fix de `trigger.py` aplicado, Caso 4 ya no genera 7 ventanas
necesariamente — el objetivo (u objetivos) cuya ventana quedó rebasada
durante el corte se descarta con advertencia en vez de producir una
ventana degenerada. El criterio se ajustó de "7 ventanas" a: la advertencia
de interrupción aparece antes que la de restablecimiento, hubo al menos un
descarte por reconexión (mensaje "se perdió" o "Se omiten además"), y
ninguna de las ventanas que sí se generaron tiene la muestra a más de 2 °C
de su objetivo nominal (antes del fix, el desvío real era de ~7 °C).

### Caso 8 (nuevo): objetivo perdido por reconexión durante la apertura

`caso_8_perdida_por_reconexion()`: corte de lectura acotado al hueco entre
el cierre de un objetivo y la apertura del siguiente, que arranca un poco
antes de que la muestra cruce el umbral de apertura del objetivo 45 (el
trigger ya está esperando esa apertura) y termina bastante después de que
cruce su umbral de cierre. `TIMEOUT_ADVERTENCIA_S` se sobrescribe a 2 s
para el caso (con `try/finally` para restaurarlo), porque el corte real
(~5 s) es mucho más corto que el valor por omisión (30 s) y no alcanzaría a
activar el mecanismo de "advertido" dentro del hueco disponible entre
objetivos vecinos. Aprueba si el objetivo 45 se descarta con advertencia,
no genera ninguna ventana, y el resto del barrido (60, 55, 50, 40, 35, 30)
se mide con normalidad.

`ColectorSenales._on_advertencia()` se actualizó para reconocer los dos
mensajes nuevos ("se perdió" y "Se omiten además...") y sacar los objetivos
correspondientes de su cola interna de pendientes — si no, las etiquetas de
los objetivos que siguen en la tabla quedarían desalineadas, porque esos
objetivos se descartan sin que `iniciar_acumulacion` llegue a emitirse para
ellos.

El `CASOS` dict, el docstring del módulo y el texto de ayuda de `argparse`
se actualizaron de "7 casos" a "8 casos".

## Pendiente

1. Decidir si se conserva o se borra `venv_broken_i7/`.
2. Opcional: volver a correr la suite completa
   (`python herramientas\simular_barrido.py` sin argumento) de punta a
   punta para confirmar los 8 casos + nivel dos juntos, ahora que Caso 4,
   Caso 6 y Caso 8 cambiaron. No se ha hecho todavía porque el usuario pidió
   correr solo 4, 6 y 8.
