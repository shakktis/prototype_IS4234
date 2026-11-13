"""Generate the sample request packs in sample_docs/ from cases.PACKS.

Run once: python make_samples.py
"""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from cases import PACKS

OUT = Path(__file__).parent / "sample_docs"
OUT.mkdir(exist_ok=True)

for pack in PACKS.values():
    c = canvas.Canvas(str(OUT / pack["file"]), pagesize=A4)
    c.setTitle(pack["title"])
    c.setAuthor("IS4234 Group 14 demonstrator")
    w, h = A4
    for title, rows in pack["pages"]:
        c.setFont("Helvetica-Bold", 9)
        c.setFillGray(0.45)
        c.drawString(50, h - 40, pack["stamp"])
        c.setFillGray(0)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(50, h - 80, "DOCUMENT: " + title)
        c.line(50, h - 90, w - 50, h - 90)
        c.setFont("Helvetica", 11)
        y = h - 125
        for k, v in rows:
            c.drawString(50, y, f"{k}: {v}")
            y -= 24
        c.setFont("Helvetica-Oblique", 8)
        c.setFillGray(0.45)
        c.drawString(50, 40, pack["stamp"] + ". Prepared for a university course demonstration.")
        c.setFillGray(0)
        c.showPage()
    c.save()
    print("wrote", pack["file"])
