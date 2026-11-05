# Instrucción para Claudia — recorte de la tesis (ejecutar sin supervisión)

Lee completo `Tesis documento/RECORTE_decisiones.md`. Esas decisiones ya están tomadas por Zarco: ejecútalas, no las rediscutas. Meta: ~115 → ~87 páginas (con figuras; sin figuras saldrá menos, es esperado).

## Git (obligatorio, en este orden)
1. `git status`. Crea la rama `recorte-tesis` desde el estado ACTUAL (incluye cambios sin commitear de `Tesis documento`).
2. Primer commit en la rama, antes de tocar nada: `tesis: estado previo al recorte` (solo archivos de `Tesis documento/`).
3. NO toques `main`, NO hagas merge, NO hagas push. NO toques código de `app/`, `herramientas/`, `esp32/` ni nada fuera de `Tesis documento/`.
4. Un commit por pasada (abajo), con mensaje descriptivo.

## Reglas de contenido
- NO inventes datos, números, fechas ni resultados. Cada cifra que quede debe existir ya en el texto actual o en `evidencia/`. Si para condensar necesitas una cifra que no encuentras, deja el texto sin esa cifra.
- NO borres contenido: lo que salga del documento muévelo a `capitulos/_recortado/<archivo_original>.tex` (sin `\input`). Es el registro personal de Zarco.
- §4.3 (Prueba integrada, `3_3_prueba.tex`) NO se reescribe ahora (se hará con los datos del 3-nov aparte). Solo arregla referencias rotas si las hay.
- Estilo: español de México, impersonal, el de la tesis actual. El asesor siempre es "M. en C. Arturo Ronquillo Arvizu", nunca "Dr.".
- Enfoque nuevo de toda la tesis: control e instrumentación orientada a software. El aporte es el sistema de automatización y su verificación, no la física.
- Frase que sustituye al Apéndice B (manual): el manual se elaboró para un operador sin conocimientos de programación y el sistema fue operado en el laboratorio por el M. en C. Arturo Ronquillo Arvizu **con el autor conectado de forma remota** (sesiones del 20 de octubre y del 3 de noviembre de 2026). NO digas "de forma autónoma": no lo fue. Link: https://github.com/EvandherZarco/automatizacion_del_osciloscopio . El tag/DOI aún no existe: deja `% TODO-TAG: citar tag de la versión entregada / DOI de Zenodo` en el .tex, sin texto visible inventado.

## Figuras
Comenta el bloque `figure` COMPLETO (desde `\begin{figure}` hasta `\end{figure}`) en TODO el documento (caps. 1–5, apéndices, incluidos diagramas y los `\input{...fig_...}`), anteponiendo a cada bloque la línea `% FIG-RECORTE`. No comentes tablas. Las `\cref{fig:...}` que queden como ?? son esperadas: NO las quites del texto.

## Pasada 1 — estructural (commit: `tesis: recorte estructural`)
Quitar/mover secciones, quitar Ap. B (y su frase en `cap1_introduccion.tex` ~l.215, sustituirla), comentar figuras, quitar 4.4.9 y las historias de 4.4 que pasan a tabla, quitar 4.2.3, 4.2.5, "Un defecto real encontrado al reconstruir las pruebas", subsecciones de 4.2.7 salvo "Recorrido completo del modo por temperatura", 3.4 "Verificación de adquisición nueva". Usar `propuesta_val_simulador.tex` en lugar de la simulación actual. Redirigir toda `\cref` a etiquetas eliminadas hacia la tabla/sección que las sustituye. Compilar (latexmk, biber). Cero errores; cero `??` salvo figuras.

## Pasada 2 — condensar y reescribir (commit: `tesis: recorte condensado`)
Todo lo marcado "a la mitad", "condensar" o "reescribir" en RECORTE_decisiones.md: 4.4 en dos tablas (fallas del entorno #1, #4, #6, #12; defectos de software por clase con su regla de diseño — la tabla de clases vive en 4.2.6 y 4.4 solo la referencia); 4.1.4 absorbe nan / guion gris / campos bloqueados / confirmación al reabrir; 4.1.5 y 4.1.6 reescritas (~1.5 págs. c/u) describiendo la versión actual del código, incluido el arreglo del testigo y el sentido caliente→frío con su justificación (microondas + enfriamiento pasivo, sin calentador controlado); frase del .mat en 4.1.7; quitar hashes de commit y fechas sesión por sesión del cuerpo; resumen y cap. 5 reescritos con el nuevo enfoque (ya NO decir "quince modos de falla" ni "doce de los quince"); trabajo a futuro a la mitad + barrido en ambos sentidos. Compilar de nuevo.

## Informe final (es lo único que Zarco va a leer)
1. Páginas antes / después (sin figuras) y estimación con figuras.
2. Lista de secciones cambiadas, una línea cada una.
3. `??` que quedan que NO sean de figuras (debería ser cero).
4. Todo lo que no tuviste claro o decidiste por tu cuenta — marcado como DUDA.
5. Revisión de 3.6 "Calibración relativa entre sensores" contra el CSV del 20-oct (S4 lee 0.2–0.6 °C más): ¿el texto lo contradice? No la corrijas si no es claro: repórtalo.
6. `git log --oneline -3`.
