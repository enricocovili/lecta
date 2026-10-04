"""Small synthetic fixtures, generated deterministically at test time.

  slides.pdf        3 landscape "slides", page 2 embeds a raster block diagram
  note.md           Markdown with inline/display math, a Mermaid block, an ASCII diagram and a relative image
  img/schema.png    the image referenced by note.md
"""

from __future__ import annotations

import io
import math
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont

MD = r"""# Sistemi lineari tempo-invarianti

Un sistema è **lineare** se vale il principio di sovrapposizione:
$T[a x_1 + b x_2] = a\,T[x_1] + b\,T[x_2]$.

La risposta all'impulso $h(t)$ caratterizza completamente un sistema LTI:

$$y(t) = \int_{-\infty}^{+\infty} h(\tau)\, x(t-\tau)\, d\tau$$

```mermaid
graph LR
  A[Ingresso x] --> B[Sistema h]
  B --> C[Uscita y]
```

Schema del prof:

```
+-------+     +-------+
| x(t)  | --> | h(t)  | --> y(t)
+-------+     +-------+
```

![schema a blocchi](img/schema.png)

- stabilità BIBO: $\int |h(t)|\,dt < \infty$
- causalità: $h(t) = 0$ per $t < 0$
"""


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - old Pillow
        return ImageFont.load_default()


def block_diagram(w: int = 900, h: int = 380) -> Image.Image:
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    f = _font(34)
    boxes = [(40, 130, 250, 250, "x(t)"), (345, 130, 555, 250, "h(t)"), (650, 130, 860, 250, "y(t)")]
    for x0, y0, x1, y1, label in boxes:
        d.rectangle((x0, y0, x1, y1), outline="black", width=5)
        d.text(((x0 + x1) / 2, (y0 + y1) / 2), label, fill="black", font=f, anchor="mm")
    for xa, xb in ((250, 345), (555, 650)):
        d.line((xa, 190, xb - 12, 190), fill="black", width=5)
        d.polygon([(xb, 190), (xb - 22, 178), (xb - 22, 202)], fill="black")
    return im


def slides_pdf() -> bytes:
    doc = fitz.open()
    w, h = 842, 595  # A4 landscape
    p1 = doc.new_page(width=w, height=h)
    p1.insert_text((60, 90), "Sistemi LTI", fontsize=36)
    p1.insert_text((60, 160), "- Linearita: principio di sovrapposizione", fontsize=20)
    p1.insert_text((60, 200), "- Tempo-invarianza: y(t - t0) = T[x(t - t0)]", fontsize=20)
    p1.insert_text((60, 240), "- Risposta all'impulso h(t)", fontsize=20)
    p2 = doc.new_page(width=w, height=h)
    p2.insert_text((60, 80), "Schema a blocchi", fontsize=32)
    p2.insert_text((60, 120), "Il sistema trasforma l'ingresso x(t) nell'uscita y(t).", fontsize=18)
    buf = io.BytesIO()
    block_diagram().save(buf, "PNG")
    p2.insert_image(fitz.Rect(120, 170, 720, 420), stream=buf.getvalue())
    p3 = doc.new_page(width=w, height=h)
    p3.insert_text((60, 80), "Convoluzione", fontsize=32)
    p3.insert_text((60, 140), "y(t) = integrale di h(tau) x(t - tau) dtau", fontsize=20)
    p3.insert_text((60, 180), "Proprieta: commutativa, associativa, distributiva.", fontsize=20)
    return doc.tobytes()


def schema_png() -> bytes:
    buf = io.BytesIO()
    block_diagram(700, 300).save(buf, "PNG")
    return buf.getvalue()


def make_all(dest: Path) -> dict[str, Path]:
    dest.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}

    def write(name: str, data: bytes) -> Path:
        p = dest / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        out[name] = p
        return p

    write("slides.pdf", slides_pdf())
    write("note.md", MD.encode())
    write("img/schema.png", schema_png())

    return out


if __name__ == "__main__":  # python -m tests.fixtures /tmp/out
    import sys

    for k, v in make_all(Path(sys.argv[1] if len(sys.argv) > 1 else "fixtures-out")).items():
        print(k, v.stat().st_size)
    _ = math.pi
