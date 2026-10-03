"""Compile service, diagnostics, figure cache, sandbox, publishing."""

from __future__ import annotations

import fitz
import pytest

from app.services import texlog

from .conftest import wait_job


async def _course(admin, name="LaTeX test", chapters=("Primo", "Secondo")):
    r = await admin.post("/api/courses", json={"name": name, "chapters": list(chapters)})
    assert r.status_code == 201, r.text
    return r.json()


async def _put(admin, cid, path, content):
    r = await admin.put(f"/api/courses/{cid}/files/content", json={"path": path, "content": content})
    assert r.status_code == 200, r.text
    return r.json()


async def test_compile_ok_and_incremental_includeonly(admin):
    c = await _course(admin)
    ch1, ch2 = c["chapters"]
    await _put(admin, c["id"], ch1["path"], "\\chapter{Primo}\nCiao $e^{i\\pi}+1=0$.\n\\begin{definition}[Gruppo]Un insieme.\\end{definition}\n\\review{controllare}\n")
    await _put(admin, c["id"], ch2["path"], "\\chapter{Secondo}\nSecondo capitolo.\n")
    r = await admin.post(f"/api/courses/{c['id']}/compile", json={"full": True})
    res = r.json()
    assert r.status_code == 200 and res["status"] == "ok", res
    assert res["includeonly"] == []
    pdf = await admin.get(res["pdf_url"])
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    text = "".join(p.get_text() for p in fitz.open(stream=pdf.content, filetype="pdf"))
    assert "Primo" in text and "Secondo capitolo" in text
    assert "Review" in text and "controllare" in text  # draft builds show review markers

    # Editing a chapter compiles only that chapter.
    r = await admin.post(f"/api/courses/{c['id']}/compile", json={"file": ch2["path"]})
    res = r.json()
    assert res["status"] == "ok" and res["includeonly"] == [ch2["path"]]
    text = "".join(p.get_text() for p in fitz.open(stream=(await admin.get(res["pdf_url"])).content, filetype="pdf"))
    assert "Secondo capitolo" in text and "controllare" not in text

    latest = (await admin.get(f"/api/courses/{c['id']}/builds/latest")).json()["build"]
    assert latest["status"] == "ok"


async def test_small_pictures_beside_their_text_compile_and_show_in_the_draft(admin):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (120, 80), (40, 90, 200)).save(buf, "PNG")
    c = await _course(admin, "Side figure", chapters=("Primo",))
    ch = c["chapters"][0]
    for name in ("schema.png", "altro.png"):
        await admin.post(f"/api/courses/{c['id']}/files/upload", params={"name": name}, content=buf.getvalue(), headers={"content-type": "application/octet-stream"})
    body = (
        "\\chapter{Primo}\nPrima.\n\n"
        "\\lectaimagewithtext[Schema del campionatore]{images/schema.png}{Il campionatore preleva $x(nT)$ ogni $T$ secondi.}\n\n"
        "\\lectaimagewithtext{}{Solo testo, l'immagine è stata tolta.}\n\n"
        "\\lectaimagepair{Uno}{images/schema.png}{Due}{images/altro.png}\n\n"
        "\\lectaimage[Da sola]{images/altro.png}\n"
    )
    await _put(admin, c["id"], ch["path"], body)
    res = (await admin.post(f"/api/courses/{c['id']}/compile", json={"full": True})).json()
    assert res["status"] == "ok", res["diagnostics"]
    pdf = await admin.get(res["pdf_url"])
    text = "".join(p.get_text() for p in fitz.open(stream=pdf.content, filetype="pdf"))
    for needle in ("Il campionatore preleva", "Schema del campionatore", "Solo testo", "Uno", "Due", "Da sola"):
        assert needle in text, needle
    blocks = (await admin.get(f"/api/courses/{c['id']}/chapters/{ch['id']}/draft")).json()["blocks"]
    # The draft is the same LaTeX: every block is drawn, the ones with a photo say so (the dark theme leaves them alone).
    assert len(blocks) == 6 and all(b["pages"] and not b["error"] for b in blocks)
    assert [b["pages"][0]["raster"] for b in blocks] == [False, False, True, False, True, True]


async def test_tables_without_rules_get_them_in_the_pdf_only(admin):
    c = await _course(admin, "Tables", chapters=("Primo",))
    ch = c["chapters"][0]
    body = (
        "\\chapter{Primo}\nUn paragrafo che precede la tabella.\n\n"
        "\\begin{tabularx}{\\linewidth}{@{}>{\\raggedright\\arraybackslash}p{0.27\\linewidth}X@{}}\n"
        "\\textbf{Servizio} & Descrizione del servizio \\\\\n\\textbf{Sicurezza} & Difende da accessi non autorizzati \\\\\n\\end{tabularx}\n"
    )
    await _put(admin, c["id"], ch["path"], body)
    res = (await admin.post(f"/api/courses/{c['id']}/compile", json={"full": True})).json()
    assert res["status"] == "ok", res["diagnostics"]
    pdf = await admin.get(res["pdf_url"])
    doc = fitz.open(stream=pdf.content, filetype="pdf")
    page = next(p for p in doc if "Descrizione del servizio" in p.get_text())
    lines = [d for d in page.get_drawings() if d["rect"].height < 2 and d["rect"].width > 100]
    assert len(lines) >= 2, "the table has its top and bottom rule in the PDF"
    # The stored chapter is untouched.
    stored = (await admin.get(f"/api/courses/{c['id']}/files/content", params={"path": ch["path"]})).json()["content"]
    assert "toprule" not in stored


async def test_errors_are_parsed_with_file_and_line(admin):
    c = await _course(admin, "Errors")
    ch = c["chapters"][0]
    await _put(admin, c["id"], ch["path"], "\\chapter{Primo}\nriga\n\\undefinedmacro\n\\ref{nope}\n")
    res = (await admin.post(f"/api/courses/{c['id']}/compile", json={"file": ch["path"]})).json()
    errors = [d for d in res["diagnostics"] if d["level"] == "error"]
    assert errors, res["diagnostics"]
    assert errors[0]["file"] == ch["path"] and errors[0]["line"] == 3
    assert "Undefined control sequence" in errors[0]["message"]


async def test_figures_precompiled_and_cached(admin):
    c = await _course(admin, "Figures")
    cid = c["id"]
    fig = "\\begin{tikzpicture}\\draw[->] (0,0) -- (2,1) node[right]{$x$};\\end{tikzpicture}\n"
    r = await admin.post(f"/api/courses/{cid}/files", json={"path": "figures/freccia.tex", "content": fig})
    assert r.status_code == 201, r.text
    ch = c["chapters"][0]
    await _put(admin, cid, ch["path"], "\\chapter{Primo}\n\\lectafigure[Una freccia]{freccia}\n")
    res = (await admin.post(f"/api/courses/{cid}/compile", json={"file": ch["path"]})).json()
    assert res["status"] == "ok", res
    assert res["figures"][0]["name"] == "freccia" and res["figures"][0]["status"] == "ok"
    res = (await admin.post(f"/api/courses/{cid}/compile", json={"file": ch["path"]})).json()
    assert res["figures"][0]["status"] == "cached"
    # Editing a figure recompiles it and targets the chapter that uses it.
    await _put(admin, cid, "figures/freccia.tex", fig.replace("(2,1)", "(3,1)"))
    res = (await admin.post(f"/api/courses/{cid}/compile", json={"file": "figures/freccia.tex"})).json()
    assert res["figures"][0]["status"] == "ok" and res["includeonly"] == [ch["path"]]
    # A broken figure is reported against its own file.
    await _put(admin, cid, "figures/freccia.tex", "\\begin{tikzpicture}\\draw (0,0) -- ;\\end{tikzpicture}")
    res = (await admin.post(f"/api/courses/{cid}/compile", json={"file": ch["path"]})).json()
    assert any(d["file"] == "figures/freccia.tex" and d["level"] == "error" for d in res["diagnostics"]), res["diagnostics"]


@pytest.mark.parametrize(
    "payload,expect",
    [
        ("\\input{/etc/passwd}", "File `/etc/passwd.tex' not found"),
        ("\\input{../../../../etc/hostname}", "File `../../../../etc/hostname.tex' not found"),
        ("\\immediate\\write18{touch /tmp/pwned}", "runsystem(touch /tmp/pwned)...disabled"),
        ("\\newwrite\\f\\immediate\\openout\\f=/tmp/evil.txt\\relax", "I can't write on file `/tmp/evil.txt'"),
        ("\\newwrite\\f\\immediate\\openout\\f=../evil.txt\\relax", "I can't write on file `../evil.txt'"),
    ],
)
async def test_sandbox_blocks_escapes(admin, payload, expect):
    """AI-generated LaTeX can't read or write outside the project, nor run commands."""
    c = await _course(admin, "Sandbox")
    ch = c["chapters"][0]
    await _put(admin, c["id"], ch["path"], "\\chapter{Primo}\n" + payload + "\n")
    res = (await admin.post(f"/api/courses/{c['id']}/compile", json={"file": ch["path"]})).json()
    log = (await admin.get(f"/api/courses/{c['id']}/builds/{res['build_id']}/log")).text
    assert expect in log, log[-3000:]
    assert "root:x:0:0" not in log


async def test_project_cannot_contain_latexmkrc(admin):
    c = await _course(admin, "RC")
    for p in ["latexmkrc", ".latexmkrc", "chapters/latexmkrc.tex", "_root.tex"]:
        r = await admin.post(f"/api/courses/{c['id']}/files", json={"path": p, "content": "system('id')"})
        assert r.status_code == 400, p


async def test_save_conflict_detection(admin):
    c = await _course(admin, "Conflict", chapters=("Uno",))
    path = c["chapters"][0]["path"]
    cur = (await admin.get(f"/api/courses/{c['id']}/files/content", params={"path": path})).json()
    await _put(admin, c["id"], path, "\\chapter{Uno}\nA\n")
    r = await admin.put(
        f"/api/courses/{c['id']}/files/content", json={"path": path, "content": "B", "base_blob": cur["blob"]}
    )
    assert r.status_code == 409


async def test_publish_flow(admin, anon, worker):
    from app.pipeline import publish as publish_pipe

    c = await _course(admin, "Pubblicato", chapters=("Alfa", "Beta"))
    cid = c["id"]
    a, b = c["chapters"]
    await _put(admin, cid, a["path"], "\\chapter{Alfa}\nTesto alfa.\n\\review{nota privata di revisione}\n\\newpage Altra pagina.\n")
    await _put(admin, cid, b["path"], "\\chapter{Beta}\nTesto beta.\n")
    assert (await admin.get(f"/api/courses/{cid}/publish")).json()["state"] == "off"

    # ON: public from now on, first build queued.
    r = await admin.post(f"/api/courses/{cid}/publish")
    assert r.json()["published"] and r.json()["state"] == "preparing"
    job = await wait_job(admin, r.json()["job_id"], timeout=240)
    assert job["status"] == "succeeded", job
    assert (await admin.get(f"/api/courses/{cid}/publish")).json()["state"] == "online"

    listing = (await anon.get("/api/public/courses")).json()
    pub = next(x for x in listing if x["slug"] == c["slug"])
    assert pub["chapter_count"] == 2
    detail = (await anon.get(f"/api/public/courses/{c['slug']}")).json()
    assert [ch["title"] for ch in detail["chapters"]] == ["Alfa", "Beta"]
    full = await anon.get(pub["pdf_url"])
    assert full.status_code == 200 and full.headers["content-type"] == "application/pdf"
    text = "".join(p.get_text() for p in fitz.open(stream=full.content, filetype="pdf"))
    assert "Testo alfa" in text and "Testo beta" in text
    assert "nota privata" not in text and "Review" not in text  # markers stripped
    ch_pdf = await anon.get(detail["chapters"][1]["pdf_url"])
    ch_text = "".join(p.get_text() for p in fitz.open(stream=ch_pdf.content, filetype="pdf"))
    assert "Testo beta" in ch_text and "Testo alfa" not in ch_text

    # Nothing changed: the automatic pass queues nothing.
    assert cid not in await publish_pipe.republish_pass(0)
    # A later edit is published automatically once the course is quiet.
    await _put(admin, cid, b["path"], "\\chapter{Beta}\nTesto beta MODIFICATO.\n")
    assert (await admin.get(f"/api/courses/{cid}/publish")).json()["state"] == "updating"
    assert cid not in await publish_pipe.republish_pass(3600)  # not quiet long enough
    assert cid in await publish_pipe.republish_pass(0)
    jobs = (await admin.get("/api/jobs")).json()
    auto = max((j for j in jobs if j["kind"] == "publish" and j.get("course_id") == cid), key=lambda j: j["id"])
    assert (await wait_job(admin, auto["id"], timeout=240))["status"] == "succeeded"
    full2 = await anon.get(pub["pdf_url"])
    assert "MODIFICATO" in "".join(p.get_text() for p in fitz.open(stream=full2.content, filetype="pdf"))
    assert cid not in await publish_pipe.republish_pass(0)

    # A broken edit: the rebuild fails and the previous PDF stays online.
    await _put(admin, cid, b["path"], "\\chapter{Beta}\n\\begin{itemize}\nrotto\n")
    assert cid in await publish_pipe.republish_pass(0)
    jobs = (await admin.get("/api/jobs")).json()
    auto = max((j for j in jobs if j["kind"] == "publish" and j.get("course_id") == cid), key=lambda j: j["id"])
    assert (await wait_job(admin, auto["id"], timeout=240))["status"] == "failed"
    state = (await admin.get(f"/api/courses/{cid}/publish")).json()
    assert state["state"] == "failed" and state["error"]
    assert (await anon.get(pub["pdf_url"])).content == full2.content
    assert cid not in await publish_pipe.republish_pass(0)  # no retry loop until something changes

    # OFF hides everything again.
    await admin.post(f"/api/courses/{cid}/unpublish")
    assert (await anon.get(pub["pdf_url"])).status_code == 404
    assert (await anon.get(f"/api/public/courses/{c['slug']}")).status_code == 404


def test_texlog_parser():
    log = "\n".join(
        [
            "(./main.tex (./preamble.tex (/usr/share/texmf/tex/latex/base/article.cls))",
            "(./chapters/01-a.tex",
            "LaTeX Warning: Reference `x' on page 1 undefined on input line 7.",
            "./chapters/01-a.tex:12: Undefined control sequence.",
            "l.12 \\foo",
            "",
            "Package hyperref Warning: Token not allowed in a PDF string,",
            "(hyperref)                removing `math shift' on input line 3.",
            "Overfull \\hbox (12.0pt too wide) in paragraph at lines 20--21",
            ")",
            ")",
        ]
    )
    d = texlog.parse(log)
    w = next(x for x in d if "Reference" in x["message"])
    assert w["file"] == "chapters/01-a.tex" and w["line"] == 7 and w["level"] == "warning"
    e = next(x for x in d if x["level"] == "error")
    assert e["file"] == "chapters/01-a.tex" and e["line"] == 12 and e["context"] == "\\foo"
    h = next(x for x in d if "hyperref" in x["message"])
    assert "removing" in h["message"] and h["line"] == 3
    assert any(x["level"] == "badbox" and x["line"] == 20 for x in d)


async def test_blocks_endpoint_typesets_once_and_returns_svg_pages():
    """/blocks runs the engine once on a wrapper the backend wrote, then turns every page into SVG with its ink box."""
    import shutil

    from app.services import latex, projects

    wd = projects.work_dir(990001, "blocks")
    shutil.rmtree(wd, ignore_errors=True)
    wd.mkdir(parents=True)
    (wd / "_blk-7.tex").write_text(
        "\\documentclass{article}\\pagestyle{empty}\\begin{document}\n"
        "\\newwrite\\o\\immediate\\openout\\o=\\jobname.lecta\n"
        "Primo blocco $x^2$.\\clearpage Secondo blocco.\\clearpage\n"
        "\\immediate\\write\\o{D}\\end{document}\n"
    )
    res = await latex.blocks({"workdir": latex.rel(wd), "job": "_blk-7", "timeout": 60})
    assert res["status"] == "ok", res["log"][-2000:]
    assert res["pages"] == 2 and res["marks"].strip() == "D"
    svgs = sorted((projects.config.latex_root / res["svg_dir"]).glob("*.svg"))
    assert [p.name for p in svgs] == ["1.svg", "2.svg"] and svgs[0].read_text().startswith("<svg")
    # pdflatex can start from a precompiled preamble: made the first time, then reused.
    (wd / "_fmt-0123456789abcdef.tex").write_text("\\documentclass{article}\\usepackage{amsmath}\\begin{document}\\end{document}\n")
    (wd / "_blk-7.tex").write_text(
        "\\documentclass{article}\\usepackage{amsmath}\\begin{document}\\pagestyle{empty}\n"
        "\\newwrite\\o\\immediate\\openout\\o=\\jobname.lecta\n"
        "Uno $\\begin{aligned}a&=b\\end{aligned}$.\\clearpage\\immediate\\write\\o{D}\\end{document}\n"
    )
    body = {"workdir": latex.rel(wd), "job": "_blk-7", "timeout": 60, "format": "_fmt-0123456789abcdef"}
    first, second = await latex.blocks(body), await latex.blocks(body)
    assert (first["status"], first["format"], first["pages"]) == ("ok", "built", 1), first["log"][-2000:]
    assert (second["status"], second["format"]) == ("ok", "cached") and "preloaded format=_fmt-0123456789abcdef" in second["log"]
    # A job name outside the protocol is refused.
    with pytest.raises(latex.CompileServiceError):
        await latex.blocks({"workdir": latex.rel(wd), "job": "../main", "timeout": 60})
    shutil.rmtree(wd, ignore_errors=True)
