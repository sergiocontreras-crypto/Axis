# Scripts del Proyecto AXIS

Este directorio contiene scripts útiles para el proyecto AXIS.

## convert_to_pdf.py

Convierte archivos Markdown a PDF con formato profesional.

### Requisitos

**Pandoc** debe estar instalado en el sistema. Opciones de instalación:

#### Opción 1: Descargar instalador
1. Ir a https://pandoc.org/installing.html
2. Descargar el instalador para Windows
3. Ejecutar el instalador
4. Reiniciar PowerShell/CMD

#### Opción 2: Usar Chocolatey
```bash
choco install pandoc
```

#### Opción 3: Usar winget
```bash
winget install --id JohnMacFarlane.Pandoc
```

### Uso

```bash
# Convertir CONTRIBUTING.md y README.md a PDF
python scripts/convert_to_pdf.py
```

Los archivos PDF se generarán en la carpeta `docs/`:
- `docs/CONTRIBUTING.pdf`
- `docs/README.pdf`

### Características

- ✅ Tabla de contenidos automática
- ✅ Formato profesional con márgenes de 2cm
- ✅ Enlaces en azul
- ✅ Tamaño de fuente 11pt
- ✅ Soporte para código y tablas

### Personalización

Para convertir otros archivos Markdown, editar la lista `files_to_convert` en el script:

```python
files_to_convert = [
    ("archivo.md", "salida.pdf"),
    ("otro.md", "docs/otro.pdf"),
]
```

### Solución de Problemas

**Error: "pandoc no está instalado"**
- Instalar Pandoc siguiendo las instrucciones arriba
- Verificar instalación: `pandoc --version`
- Reiniciar terminal después de instalar

**Error: "No se encontró el archivo"**
- Verificar que el archivo .md existe
- Verificar la ruta relativa al directorio del proyecto

**PDF sin formato**
- Pandoc usa el motor PDF por defecto si wkhtmltopdf no está disponible
- El PDF se generará de todas formas, pero con formato más simple
