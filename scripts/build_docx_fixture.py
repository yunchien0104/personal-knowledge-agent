from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "fixtures" / "docx" / "synthetic-notes-import.docx"
IMAGE = ROOT / "fixtures" / "docx" / "synthetic-unsupported-image.png"


def set_font(run, name="Arial", size=10, bold=False):
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), name)
    run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), name)


image = Image.new("RGB", (720, 180), "#EEF2FF")
draw = ImageDraw.Draw(image)
draw.rectangle((3, 3, 716, 176), outline="#4F46E5", width=4)
draw.text((30, 70), "Synthetic unsupported image fixture", fill="#312E81")
image.save(IMAGE)

doc = Document()
title_style = doc.styles["Title"]
title_ppr = title_style.element.get_or_add_pPr()
title_border = title_ppr.find(qn("w:pBdr"))
if title_border is not None:
    title_ppr.remove(title_border)
section = doc.sections[0]
section.top_margin = Inches(0.75)
section.bottom_margin = Inches(0.75)
section.left_margin = Inches(0.85)
section.right_margin = Inches(0.85)

title = doc.add_paragraph(style="Title")
title.alignment = WD_ALIGN_PARAGRAPH.LEFT
title_run = title.add_run("Synthetic Notes Import Fixture")
set_font(title_run, size=20, bold=True)
title_run.font.color.rgb = RGBColor(0, 0, 0)

intro = doc.add_paragraph()
set_font(intro.add_run("This document contains invented content for DOCX import tests. It includes headings, emphasis, lists, a table, a formula source and one intentionally unsupported image."), size=10)

heading = doc.add_paragraph(style="Heading 1")
set_font(heading.add_run("Structured content"), size=15, bold=True)

p = doc.add_paragraph()
set_font(p.add_run("The importer should preserve "), size=10)
set_font(p.add_run("bold text"), size=10, bold=True)
set_font(p.add_run(" and ordinary paragraphs without treating this content as production data."), size=10)

for item in ["First synthetic bullet", "Second synthetic bullet"]:
    paragraph = doc.add_paragraph(style="List Bullet")
    set_font(paragraph.add_run(item), size=10)

heading = doc.add_paragraph(style="Heading 2")
set_font(heading.add_run("Spreadsheet sample"), size=12, bold=True)
table = doc.add_table(rows=4, cols=4)
table.style = "Table Grid"
rows = [
    ["Item", "Q1", "Q2", "Formula source"],
    ["Alpha", "12", "14", "=SUM(B2:C2)"],
    ["Beta", "8", "11", "=SUM(B3:C3)"],
    ["Total", "20", "25", "=SUM(D2:D3)"],
]
for row, values in zip(table.rows, rows):
    for cell, value in zip(row.cells, values):
        cell.text = value
        for run in cell.paragraphs[0].runs:
            set_font(run, size=9, bold=row is table.rows[0])

heading = doc.add_paragraph(style="Heading 2")
set_font(heading.add_run("Equation source"), size=12, bold=True)
eq = doc.add_paragraph()
set_font(eq.add_run("LaTeX: E = mc^2"), size=10)

heading = doc.add_paragraph(style="Heading 2")
set_font(heading.add_run("Unsupported image case"), size=12, bold=True)
doc.add_picture(str(IMAGE), width=Inches(5.6))
caption = doc.add_paragraph("The first Notes release must report this image as unsupported.")
caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
for run in caption.runs:
    set_font(run, size=9)

doc.core_properties.title = "Synthetic Notes Import Fixture"
doc.core_properties.subject = "Development fixture"
doc.core_properties.author = "Synthetic Fixture Generator"
doc.save(OUTPUT)
print(OUTPUT)
