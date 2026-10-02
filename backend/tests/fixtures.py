"""Small synthetic fixtures, generated deterministically at test time.

  slides.pdf        3 landscape "slides", page 2 embeds a raster block diagram
  appunti.jpg       handwriting-like photo of a page on a table, EXIF-rotated
  appunti.heic      the same photo as HEIC (if the encoder is available)
  note.md           Markdown with inline/display math, a Mermaid block, an ASCII diagram and a relative image
  img/schema.png    the image referenced by note.md
  mixed.zip         all of the above in nested folders + junk (__MACOSX, .DS_Store) + an unsupported file
  zipslip.zip, zipbomb.zip   malicious archives
"""

from __future__ import annotations

import io
import math
import random
import zipfile
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


def handwriting_photo(fmt: str = "JPEG") -> bytes:
    rnd = random.Random(42)
    page = Image.new("RGB", (1100, 1500), (246, 242, 230))
    d = ImageDraw.Draw(page)
    f = _font(46)
    lines = [
        "Appunti 12/3 - sistemi LTI",
        "stabilita BIBO <=> h assolutamente",
        "integrabile",
        "esempio: h(t) = e^(-t) u(t)",
        "  -> stabile e causale",
        "convoluzione: y = h * x",
    ]
    y = 110
    for line in lines:
        x = 90 + rnd.randint(-10, 10)
        for ch in line:
            d.text((x, y + rnd.randint(-3, 3)), ch, fill=(20, 30, 110), font=f)
            x += d.textlength(ch, font=f) + rnd.uniform(-1, 2)
        y += 120 + rnd.randint(-8, 8)
    # A little hand-drawn arrow diagram.
    d.rectangle((150, 900, 400, 1050), outline=(20, 30, 110), width=5)
    d.rectangle((650, 900, 900, 1050), outline=(20, 30, 110), width=5)
    d.line((400, 975, 640, 975), fill=(20, 30, 110), width=5)
    d.polygon([(650, 975), (625, 962), (625, 988)], fill=(20, 30, 110))
    page = page.rotate(4, expand=True, fillcolor=(80, 60, 45))
    table = Image.new("RGB", (page.width + 260, page.height + 260), (80, 60, 45))
    table.paste(page, (130, 130))
    # Stored sideways with an EXIF orientation tag, like a phone photo.
    stored = table.rotate(90, expand=True)
    exif = Image.Exif()
    exif[274] = 6
    buf = io.BytesIO()
    if fmt == "HEIC":
        stored.save(buf, "HEIF", quality=80, exif=exif.tobytes())
    else:
        stored.save(buf, "JPEG", quality=85, exif=exif.tobytes())
    return buf.getvalue()


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
    write("appunti.jpg", handwriting_photo("JPEG"))
    try:
        write("appunti.heic", handwriting_photo("HEIC"))
    except Exception:  # pragma: no cover - encoder missing
        pass
    write("note.md", MD.encode())
    write("img/schema.png", schema_png())

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Lezione 3 - Sistemi LTI/slides.pdf", out["slides.pdf"].read_bytes())
        z.writestr("Lezione 3 - Sistemi LTI/appunti.jpg", out["appunti.jpg"].read_bytes())
        z.writestr("Lezione 3 - Sistemi LTI/note.md", MD.encode())
        z.writestr("Lezione 3 - Sistemi LTI/img/schema.png", out["img/schema.png"].read_bytes())
        z.writestr("__MACOSX/Lezione 3 - Sistemi LTI/._slides.pdf", b"\x00\x05\x16\x07junk")
        z.writestr("Lezione 3 - Sistemi LTI/.DS_Store", b"\x00\x00\x00\x01Bud1")
        z.writestr("Lezione 3 - Sistemi LTI/setup.exe", b"MZ\x90\x00" + b"\x00" * 100)
    write("mixed.zip", buf.getvalue())

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../../evil.txt", b"escape")
        z.writestr("/abs/evil2.txt", b"escape")
        z.writestr("ok/fine.md", b"# ok\n")
    write("zipslip.zip", buf.getvalue())

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        with z.open("bomb.txt", "w", force_zip64=True) as f:
            chunk = b"\x00" * (1024 * 1024)
            for _ in range(64):
                f.write(chunk)
    write("zipbomb.zip", buf.getvalue())
    return out


if __name__ == "__main__":  # python -m tests.fixtures /tmp/out
    import sys

    for k, v in make_all(Path(sys.argv[1] if len(sys.argv) > 1 else "fixtures-out")).items():
        print(k, v.stat().st_size)
    _ = math.pi
