"""
Script simple para convertir Markdown a PDF usando reportlab
No requiere instalaciones externas complicadas
"""

import os
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.enums import TA_LEFT, TA_CENTER
import re


def markdown_to_pdf_simple(markdown_file: str, output_file: str = None):
    """
    Convierte Markdown a PDF de forma simple usando reportlab
    
    Args:
        markdown_file: Ruta al archivo .md
        output_file: Ruta del archivo PDF de salida
    """
    if not os.path.exists(markdown_file):
        print(f"❌ Error: No se encontró {markdown_file}")
        return False
    
    if output_file is None:
        output_file = markdown_file.replace('.md', '.pdf')
    
    try:
        # Leer contenido
        with open(markdown_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Crear PDF
        doc = SimpleDocTemplate(
            output_file,
            pagesize=letter,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=18,
        )
        
        # Estilos
        styles = getSampleStyleSheet()
        
        # Estilo para título principal
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            textColor='#2c3e50',
            spaceAfter=30,
            alignment=TA_CENTER,
        )
        
        # Estilo para encabezados
        h1_style = ParagraphStyle(
            'CustomH1',
            parent=styles['Heading1'],
            fontSize=18,
            textColor='#2c3e50',
            spaceAfter=12,
            spaceBefore=12,
        )
        
        h2_style = ParagraphStyle(
            'CustomH2',
            parent=styles['Heading2'],
            fontSize=14,
            textColor='#34495e',
            spaceAfter=10,
            spaceBefore=10,
        )
        
        h3_style = ParagraphStyle(
            'CustomH3',
            parent=styles['Heading3'],
            fontSize=12,
            textColor='#7f8c8d',
            spaceAfter=8,
            spaceBefore=8,
        )
        
        # Estilo para texto normal
        normal_style = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=10,
            leading=14,
        )
        
        # Estilo para código
        code_style = ParagraphStyle(
            'CustomCode',
            parent=styles['Code'],
            fontSize=9,
            fontName='Courier',
            leftIndent=20,
            backgroundColor='#f4f4f4',
        )
        
        # Procesar contenido
        story = []
        lines = content.split('\n')
        
        in_code_block = False
        code_buffer = []
        
        for line in lines:
            # Bloques de código
            if line.startswith('```'):
                if in_code_block:
                    # Fin del bloque de código
                    code_text = '\n'.join(code_buffer)
                    story.append(Paragraph(f'<font name="Courier" size="8">{code_text}</font>', code_style))
                    story.append(Spacer(1, 0.2*inch))
                    code_buffer = []
                in_code_block = not in_code_block
                continue
            
            if in_code_block:
                code_buffer.append(line.replace('<', '&lt;').replace('>', '&gt;'))
                continue
            
            # Títulos
            if line.startswith('# '):
                text = line[2:].strip()
                if len(story) == 0:
                    story.append(Paragraph(text, title_style))
                else:
                    story.append(Paragraph(text, h1_style))
                story.append(Spacer(1, 0.2*inch))
            
            elif line.startswith('## '):
                text = line[3:].strip()
                story.append(Paragraph(text, h2_style))
                story.append(Spacer(1, 0.15*inch))
            
            elif line.startswith('### '):
                text = line[4:].strip()
                story.append(Paragraph(text, h3_style))
                story.append(Spacer(1, 0.1*inch))
            
            # Listas
            elif line.startswith('- ') or line.startswith('* '):
                text = '• ' + line[2:].strip()
                story.append(Paragraph(text, normal_style))
            
            elif re.match(r'^\d+\. ', line):
                text = line.strip()
                story.append(Paragraph(text, normal_style))
            
            # Líneas vacías
            elif line.strip() == '':
                story.append(Spacer(1, 0.1*inch))
            
            # Texto normal
            else:
                if line.strip():
                    # Procesar negritas y enlaces básicos
                    text = line.strip()
                    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
                    text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
                    text = re.sub(r'`(.*?)`', r'<font name="Courier" size="9">\1</font>', text)
                    
                    story.append(Paragraph(text, normal_style))
        
        # Generar PDF
        doc.build(story)
        
        print(f"✓ PDF generado: {output_file}")
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Función principal"""
    print("=" * 60)
    print("Conversor Simple de Markdown a PDF")
    print("=" * 60)
    
    # Crear carpeta docs
    os.makedirs("docs", exist_ok=True)
    
    # Convertir archivos
    files = [
        ("CONTRIBUTING.md", "docs/CONTRIBUTING.pdf"),
        ("README.md", "docs/README.pdf"),
    ]
    
    success = 0
    for md_file, pdf_file in files:
        if os.path.exists(md_file):
            print(f"\nConvirtiendo {md_file}...")
            if markdown_to_pdf_simple(md_file, pdf_file):
                success += 1
        else:
            print(f"⚠️  No encontrado: {md_file}")
    
    print("\n" + "=" * 60)
    print(f"Completado: {success}/{len(files)} archivos")
    print("=" * 60)


if __name__ == "__main__":
    main()
