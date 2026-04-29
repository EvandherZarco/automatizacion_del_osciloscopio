# Estructura del proyecto de tesis

```
tesis/
├── main.tex                          ← archivo principal, compilar este
├── referencias.bib                   ← bibliografía (BibLaTeX)
├── figuras/                          ← CREAR esta carpeta, aquí van tus imágenes
│   ├── escudo_unam.png
│   ├── escudo_fi.png
│   └── ...
└── capitulos/
    ├── portada.tex
    ├── dedicatoria.tex
    ├── agradecimientos.tex
    ├── resumen.tex
    ├── cap1_fundamentos.tex          ← EMPIEZA AQUÍ
    ├── cap2_arreglo_experimental.tex
    ├── cap3_resultados.tex           ← requiere láser funcionando
    └── cap4_conclusiones.tex         ← al final
```

## Cómo compilar en VS Code

1. Instala la extensión **LaTeX Workshop**
2. Instala **TeX Live** (Linux/Mac) o **MiKTeX** (Windows)
3. Abre `main.tex` y presiona `Ctrl+Alt+B` para compilar
4. La receta de compilación recomendada: `pdflatex → biber → pdflatex × 2`

## Configura la receta en settings.json de VS Code

```json
"latex-workshop.latex.recipes": [
  {
    "name": "pdflatex + biber",
    "tools": ["pdflatex", "biber", "pdflatex", "pdflatex"]
  }
],
"latex-workshop.latex.tools": [
  {
    "name": "pdflatex",
    "command": "pdflatex",
    "args": ["-synctex=1", "-interaction=nonstopmode", "-file-line-error", "%DOC%"]
  },
  {
    "name": "biber",
    "command": "biber",
    "args": ["%DOCFILE%"]
  }
]
```

## Orden de escritura sugerido

1. **Cap 1 sección 1.1** — El efecto fotoacústico (hoy)
2. **Cap 1 sección 1.2** — Fotoacústica en líquidos de baja absorción
3. **Cap 1 secciones 1.3 y 1.4** — Necesidad y técnicas actuales
4. **Cap 2 secciones 2.1–2.3** — Celda, equipos, sistema de energía
5. **Cap 2 secciones 2.4–2.5** — Automatización (cuando el láser funcione)
6. **Cap 3** — Resultados (cuando el láser funcione)
7. **Cap 4** — Conclusiones (al final de todo)
8. **Resumen** — Lo último que se escribe
