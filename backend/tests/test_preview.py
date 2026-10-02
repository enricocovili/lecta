"""HTML draft preview and the LaTeX source downloads."""

from __future__ import annotations

import io
import zipfile

CHAPTER = r"""\chapter{Campionamento}

\section{Il teorema}
Un segnale $x(t)$ a banda limitata si ricostruisce se $f_s > 2B$, con
\[ x(t) = \sum_n x(nT)\,\mathrm{sinc}(t/T). \label{eq:x} \]

\begin{definition}[Frequenza di Nyquist]
La frequenza $2B$ è detta \emph{frequenza di Nyquist}.
\end{definition}

\begin{itemize}
\item primo punto
\item secondo \textbf{punto}
\end{itemize}

\lectaimage[Schema del campionatore]{images/schema.png}

\review{Verificare la formula}

\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}

\begin{proof}
Ovvio. \href{javascript:alert(1)}{cattivo} \href{https://example.org}{buono}
\end{proof}
"""

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc0f01f0005000201a5f645400000000049454e44ae426082")


async def _course(admin, name):
    c = (await admin.post("/api/courses", json={"name": name, "language": "it", "chapters": ["Campionamento"]})).json()
    ch = c["chapters"][0]
    await admin.put(f"/api/courses/{c['id']}/files/content", json={"path": ch["path"], "content": CHAPTER})
    await admin.post(f"/api/courses/{c['id']}/files/upload", params={"name": "schema.png"}, content=PNG, headers={"content-type": "application/octet-stream"})
    return c, ch


async def test_chapter_preview_html(admin):
    c, ch = await _course(admin, "Preview")
    r = await admin.get(f"/api/courses/{c['id']}/chapters/{ch['id']}/preview")
    assert r.status_code == 200, r.text
    p = r.json()
    html = p["html"]
    assert p["chapter"]["id"] == ch["id"] and p["toc"] == [{"id": f"ch{ch['id']}-s3", "title": "Il teorema", "level": 1, "line": 3}]
    # Maths stays TeX for KaTeX, with the source in data-tex (labels stripped).
    assert 'class="math inline" data-tex="x(t)"' in html and 'data-tex="f_s &gt; 2B"' in html
    assert "\\label" not in html and "sinc" in html
    # Blocks map back to source lines.
    assert '<h2 data-line="3"' in html and 'data-line="4"' in html
    # Theorem boxes, review notes, pictures, drawings.
    assert 'class="thm thm-definition"' in html and "Definizione</span>" in html and "Frequenza di Nyquist</span>" in html
    assert 'class="thm thm-proof"' in html and "Dimostrazione" in html
    assert 'class="review-note"' in html and "Verificare la formula" in html
    assert f'src="/api/courses/{c["id"]}/files/raw?path=images/schema.png"' in html and "Schema del campionatore</figcaption>" in html
    assert 'class="placeholder"' in html and any("TikZ" in w for w in p["warnings"])
    # Sanitised.
    assert "javascript:" not in html and 'href="https://example.org"' in html and "<script" not in html
    whole = (await admin.get(f"/api/courses/{c['id']}/preview")).json()
    assert len(whole["chapters"]) == 1 and whole["chapters"][0]["html"] == html


async def test_missing_image_is_a_placeholder_not_a_broken_link(admin):
    c = (await admin.post("/api/courses", json={"name": "Preview 2", "chapters": ["Uno"]})).json()
    ch = c["chapters"][0]
    await admin.put(f"/api/courses/{c['id']}/files/content", json={"path": ch["path"], "content": "\\chapter{Uno}\n\\lectaimage[x]{images/manca.png}\n"})
    p = (await admin.get(f"/api/courses/{c['id']}/chapters/{ch['id']}/preview")).json()
    assert "placeholder" in p["html"] and "manca.png" in p["html"] and any("non trovata" in w for w in p["warnings"])


async def test_source_zip_download(admin, anon):
    c, ch = await _course(admin, "Sorgente")
    r = await admin.get(f"/api/courses/{c['id']}/source.zip")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert {"README.txt", "main.tex", "preamble.tex", ch["path"], "images/schema.png"} <= names
    assert b"lectaimage" in z.read("preamble.tex") and b"lectapublish" not in z.read("preamble.tex").split(b"\n")[0]
    assert (await anon.get(f"/api/courses/{c['id']}/source.zip")).status_code == 404
    assert (await anon.get(f"/api/courses/{c['id']}/chapters/{ch['id']}/preview")).status_code == 404


async def test_public_source_download(admin, anon):
    from .test_public_redesign import _fake_publish
    from app.db import SessionLocal
    from app.models import Course, Publication
    from app.services import blobs
    from app.services.source_export import build_source_zip

    c = await _fake_publish(admin, "Sorgente Pubblico")
    assert (await anon.get(f"/api/public/courses/{c['slug']}")).json()["source_url"] is None  # published before this feature
    assert (await anon.get(f"/api/public/courses/{c['slug']}/source.zip")).status_code == 404
    data = build_source_zip({"main.tex": b"\\documentclass{report}", "preamble.tex": b"\\def\\lectapublish{1}\n"}, name="X", public=True)
    async with SessionLocal() as db:
        course = await db.get(Course, c["id"])
        pub = await db.get(Publication, course.current_publication_id)
        pub.source_blob, pub.source_size = blobs.put_bytes(data), len(data)
        await db.commit()
    info = (await anon.get(f"/api/public/courses/{c['slug']}")).json()
    assert info["source_url"] == f"/api/public/courses/{c['slug']}/source.zip" and info["source_size"] == len(data)
    r = await anon.get(info["source_url"])
    assert r.status_code == 200 and zipfile.ZipFile(io.BytesIO(r.content)).read("main.tex").startswith(b"\\documentclass")


def test_rules_are_added_only_to_tables_without_them():
    from app.services.tables import add_rules

    plain = "Testo.\n\n\\begin{tabularx}{\\linewidth}{@{}lX@{}}\nA & uno \\\\\nB & due \\\\\n\\end{tabularx}\n"
    ruled = add_rules(plain)
    assert ruled.count("\n") == plain.count("\n")  # nothing moves: the lines of the errors stay right
    assert "\\noindent \\begin{tabularx}" in ruled and "\\toprule" in ruled and ruled.rstrip().endswith("\\bottomrule \\end{tabularx}")
    for own in (
        "\\begin{tabular}{ll}\\toprule a & b \\\\ \\bottomrule\\end{tabular}", "\\begin{tabular}{ll}\\hline a & b \\\\ \\hline\\end{tabular}",
        "\\begin{tabular}{l|l} a & b \\\\\\end{tabular}",
    ):
        assert add_rules(own) == own  # the author's own rules are respected
    assert add_rules(ruled) == ruled  # idempotent
