"""把 pptx 渲染成逐页 PNG（经 PowerPoint 导出 PDF，再用 PyMuPDF 转图）。"""
import os, sys, time
import win32com.client

PPTX = os.path.abspath(sys.argv[1])
OUT = os.path.abspath(sys.argv[2])
os.makedirs(OUT, exist_ok=True)
PDF = os.path.join(OUT, "deck.pdf")
if os.path.exists(PDF):
    os.remove(PDF)

app = win32com.client.Dispatch("PowerPoint.Application")
pres = app.Presentations.Open(PPTX, WithWindow=False, ReadOnly=True)
pres.SaveAs(PDF, 32)  # ppSaveAsPDF
pres.Close()
try:
    app.Quit()
except Exception:
    pass

import fitz
doc = fitz.open(PDF)
for i, page in enumerate(doc, 1):
    pix = page.get_pixmap(dpi=110)
    p = os.path.join(OUT, f"p{i:02d}.png")
    pix.save(p)
    print(p, pix.width, pix.height)
