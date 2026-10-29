"""
Script para convertir archivos Markdown a PDF
Utiliza pypandoc (wrapper de Pandoc) para generar PDFs con formato profesional
"""

import os
import subprocess
from pathlib import Path


def check_pandoc_installed():
    """Verifica si Pandoc está instalado"""
    try:
        result = subprocess.run(['pandoc', '--version'], 
                              capture_output=True, 
                              text=True)
        if result.returncode == 0:
            print("✓ Pandoc está instalado")
            return True
    except FileNotFoundError:
        pass
    
    print("❌ Pandoc no está instalado")
    print("\nPara instalar Pandoc:")
    print("1. Descargar desde: https://pandoc.org/installing.html")
    print("2. O usar chocolatey: choco install pandoc")
    print("3. O usar winget: winget install --id JohnMacFarlane.Pandoc")
    return False


def convert_markdown_to_pdf(markdown_file: str, output_file: str = None):
    """
    Convierte un archivo Markdown a PDF usando Pandoc
    
    Args:
        markdown_file: Ruta al archivo .md
        output_file: Ruta del archivo PDF de salida (opcional)
    """
    # Verificar que el archivo existe
    if not os.path.exists(markdown_file):
        print(f"❌ Error: No se encontró el archivo {markdown_file}")
        return False
    
    # Generar nombre del archivo de salida si no se proporciona
    if output_file is None:
        output_file = markdown_file.replace('.md', '.pdf')
    
    try:
        # Comando pandoc con opciones para mejor formato
        cmd = [
            'pandoc',
            markdown_file,
            '-o', output_file,
            '--pdf-engine=wkhtmltopdf',  # Motor PDF alternativo
            '--toc',  # Tabla de contenidos
            '--toc-depth=3',  # Profundidad de TOC
            '-V', 'geometry:margin=2cm',  # Márgenes
            '-V', 'fontsize=11pt',  # Tamaño de fuente
            '-V', 'linkcolor=blue',  # Color de enlaces
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            print(f"✓ PDF generado exitosamente: {output_file}")
            return True
        else:
            # Si falla con wkhtmltopdf, intentar con motor por defecto
            print("⚠️  Intentando con motor PDF por defecto...")
            cmd_simple = [
                'pandoc',
                markdown_file,
                '-o', output_file,
                '--toc',
                '-V', 'geometry:margin=2cm',
            ]
            result = subprocess.run(cmd_simple, capture_output=True, text=True)
            
            if result.returncode == 0:
                print(f"✓ PDF generado exitosamente: {output_file}")
                return True
            else:
                print(f"❌ Error al generar PDF: {result.stderr}")
                return False
        
    except Exception as e:
        print(f"❌ Error al generar PDF: {e}")
        return False


def main():
    """Función principal para convertir archivos del proyecto"""
    
    print("=" * 60)
    print("Conversor de Markdown a PDF - Proyecto AXIS")
    print("=" * 60)
    
    # Verificar que Pandoc esté instalado
    if not check_pandoc_installed():
        return
    
    # Archivos a convertir
    files_to_convert = [
        ("CONTRIBUTING.md", "docs/CONTRIBUTING.pdf"),
        ("README.md", "docs/README.pdf"),
    ]
    
    # Crear carpeta docs si no existe
    os.makedirs("docs", exist_ok=True)
    
    # Convertir cada archivo
    success_count = 0
    for markdown_file, pdf_file in files_to_convert:
        if os.path.exists(markdown_file):
            print(f"\nConvirtiendo {markdown_file}...")
            if convert_markdown_to_pdf(markdown_file, pdf_file):
                success_count += 1
        else:
            print(f"⚠️  Archivo no encontrado: {markdown_file}")
    
    print("\n" + "=" * 60)
    print(f"Conversión completada: {success_count}/{len(files_to_convert)} archivos")
    print("Los PDFs están en la carpeta 'docs/'")
    print("=" * 60)


if __name__ == "__main__":
    main()
