"""The LaTeX project of a course as a zip (working version download, and the public source download)."""

from __future__ import annotations

import io
import re
import zipfile
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Course
from . import blobs, projects, templates

README = """{name}
{rule}

LaTeX project exported from Lecta on {date}.

  main.tex        document class, layout and the list of chapters
  preamble.tex    packages and the environments used by the chapters
  chapters/       one file per chapter
  images/         pictures
  figures/        TikZ drawings (\\lectafigure)

Build:  pdflatex main.tex   (twice, for the table of contents)   — or latexmk -pdf main.tex
Needs a full TeX Live (tikz, pgfplots, tcolorbox, circuitikz, ...).{public}
"""


def build_source_zip(files: dict[str, bytes], *, name: str, public: bool = False) -> bytes:
    """A zip of the project. `files` maps path → content (preamble.tex included)."""
    buf = io.BytesIO()
    now = datetime.now(UTC)
    note = "\nThis is the published version: review notes are hidden (see the first line of preamble.tex)." if public else ""
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("README.txt", README.format(name=name, rule="=" * len(name), date=now.strftime("%Y-%m-%d"), public=note))
        for path, data in sorted(files.items()):
            z.writestr(zipfile.ZipInfo(path, date_time=now.timetuple()[:6]), data, compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()


async def project_files(db: AsyncSession, course: Course, *, public: bool = False) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for pf in await projects.get_files(db, course.id):
        files[pf.path] = blobs.read_bytes(pf.blob)
    pre = templates.with_compat(await projects.preamble_for(db, course))
    if public:
        pre = "\\def\\lectapublish{1}\n" + pre
    files["preamble.tex"] = pre.encode()
    return files



def zip_name(course: Course) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", course.slug) + "-latex.zip"
