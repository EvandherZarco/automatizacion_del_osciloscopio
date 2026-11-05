# Recorte de la tesis — decisiones (sesión 4-nov-2026)
Meta: 115 → ~87 págs. NO se ha modificado ningún .tex todavía; primero se decide todo, luego se edita en la rama `recorte-tesis`.

## Decidido
- **Ap. B Manual de usuario: FUERA.** Se sustituye por 1–2 frases de evidencia (Arturo, que no programa, operó el sistema solo el 20-oct y el 3-nov con el manual) + link al repo público en GitHub (de preferencia release con DOI de Zenodo). Reescribir la frase de cap1_introduccion.tex (~l.215) que presenta el Ap. B.
- **4.4 Fallas (21 → ~6–7 págs.), dos tablas:**
  1. Fallas del entorno (fuera del programa): #1 pérdida de lectura de temperatura, #4 buffer duplicado, #6 artefacto del umbral, #12 latencia de la bandera. Tabla + explicación breve de #4 y #6.
  2. Defectos de software agrupados por clase, con la regla de diseño que salió de cada una: estado anunciado vs. confirmado (8, 13); valor obsoleto como vigente (2, 10); metadatos incompletos (3, 5, 11); escritura silenciosa (14); supuesto no medido (7, 15); comunicación sin arranque explícito (9). + un párrafo de método (auditoría, 51 pruebas sin hardware, verificación de sesiones).
  - Las historias completas y los hashes se quedan en el repo.
  - #15 sale como historia (Claudia ya la corrigió; commitear el arreglo antes de mandar a Arturo).
  - Las correcciones que hoy son funciones (nan sin sensor, guion gris, campos bloqueados, confirmación al reabrir) se describen como diseño en 4.1.4 Protecciones / 4.1.7 Formato de datos.
  - 4.4.9 "el punto perturbado": fuera.
- **Resumen y conclusiones:** se reescriben con el nuevo enfoque (ya no "quince modos de falla"). Enfoque general de la tesis: control e instrumentación orientada a software.

- **4.1 Sistema (17 → ~11):** 4.1.1 Arquitectura a la mitad; 4.1.2 Decisiones de diseño ~1 pág. (no cortar más: es de lo más valioso con el enfoque de software); 4.1.3 Interfaz: texto se queda; 4.1.4 Protecciones crece (absorbe nan, guion gris, campos bloqueados, confirmación al reabrir); 4.1.5 Secuencia y 4.1.6 Verificación se reescriben (~1.5 c/u) con el arreglo del testigo; 4.1.7 Formato: tabla se queda, prosa a la mitad + frase del .mat.
- **Sentido del barrido:** el código quedó de caliente a frío. Documentarlo en 4.1.5 como decisión de diseño: el agua se calienta en microondas y se enfría pasivamente; no hay calentador controlado en la celda, así que la rampa de enfriamiento es la única monótona disponible. Trabajo a futuro: soportar ambos sentidos (calentamiento y enfriamiento) cuando haya calentamiento controlado.
- **Figuras (TODO el documento, caps. 1–5 y apéndices, incluidos diagramas):** comentar el bloque `figure` completo (no solo \includegraphics). Zarco las reacomoda a mano después. Las \cref{fig:...} saldrán como ?? mientras tanto: esperado. El conteo de páginas sin figuras NO es el real.
- **Cap. 5 (9 → ~7):** conclusiones reescritas con el nuevo enfoque y sin repetir el cap. 4; trabajo a futuro a la mitad + barrido en ambos sentidos.
- **Hashes de commit y fechas sesión por sesión:** fuera del cuerpo; citar el repo una vez con el tag de la versión entregada.
- **Dedicatoria:** dedicatoria.tex es solo una plantilla vacía ("[Dedicatoria]") comentada en main.tex. Lo de "mi parte + el párrafo que pidió Arturo" son los Agradecimientos, que sí se cargan. No hay nada que hacer.

## Mapa cerrado (4-nov-2026, 21:00)
Siguiente paso: ejecutar el recorte en la rama `recorte-tesis` (main intacto), en dos pasadas (estructural, luego condensar), compilando después de cada una. Escribir §4.3 con los datos del 3-nov después del recorte.
