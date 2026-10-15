"""0034: the stored courses become plain LaTeX (DECISIONS 139)."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path

import asyncpg

import app.config as cfg

from .test_rename_migrations import _put_blob, run, scratch_db  # noqa: F401  (scratch_db is a fixture)


def _migration():
    path = Path(__file__).parent.parent / "alembic" / "versions" / "0034_plain_latex.py"
    spec = importlib.util.spec_from_file_location("mig0034", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_text_is_rewritten_into_standard_latex():
    m = _migration()
    out = m.rewrite_text("Prima \\lectaimage[Sola]{images/b.png} dopo\n\\review{da controllare}\n"
                         "\\lectaimagewithtext{images/a.png}{Spiega \\review {qui}.}\n\\begin{itemize}\n\\tightlist\n\\item x\n\\end{itemize}\n",
                         (0.42, 0.16))
    assert "\\lecta" not in out and "\\review" not in out and "tightlist" not in out
    assert "\\includegraphics[max width=0.42\\linewidth,max height=0.16\\textheight]{images/b.png}\n\\caption{Sola}" in out
    assert "\\todo[inline]{da controllare}" in out and "Spiega \\todo[inline]{qui}." in out
    assert "\\begin{minipage}[c]{0.57\\linewidth}" in out
    main = ("\\documentclass[11pt,a4paper,oneside]{report}\n\\def\\lectalang{italian}\n\\input{preamble}\n\n"
            "% The editor compiles only the chapter being edited.\n\\ifdefined\\lectaincludeonly\\includeonly{\\lectaincludeonly}\\fi\n\n"
            "\\begin{document}\n")
    assert m.rewrite_text(main, m.DEFAULT_SIZE) == "\\documentclass[11pt,a4paper,oneside]{report}\n\\input{preamble}\n\n\n\\begin{document}\n"
    assert m.rewrite_text(out, (0.42, 0.16)) == out  # idempotent
    for text in ("\\reviewer{x}", "Una review del testo.", "\\lectaimagex{a}"):
        assert m.rewrite_text(text, m.DEFAULT_SIZE) == text


def test_preambles_lose_the_lecta_macros():
    m = _migration()
    copy = m.OLD_DEFAULT.replace("max width=0.5\\linewidth,max height=0.22\\textheight", "max width=0.42\\linewidth,max height=0.16\\textheight")
    assert m.image_size(copy) == (0.42, 0.16) and m.image_size(None) == m.DEFAULT_SIZE
    assert m.new_override(copy, "it") is None  # a copy of the old default: the course follows the new one
    own = m.new_override(m.OLD_DEFAULT + "\\usepackage{siunitx}\n", "it")
    assert own is not None and "\\usepackage{siunitx}" in own and "\\newtheorem{definition}[theorem]{Definizione}" in own
    for macro in ("\\lecta", "\\review", "\\tightlist"):
        assert macro not in own, macro
    assert "{Definition}" in m.new_override(m.OLD_DEFAULT + "\\usepackage{siunitx}\n", "en")


def test_stored_courses_snapshots_and_notes_are_rewritten(scratch_db, tmp_path, monkeypatch):
    monkeypatch.setattr(cfg.Config, "data_dir", tmp_path)
    m = _migration()
    dsn, upgrade_to = scratch_db
    upgrade_to("0033")

    main_old = "\\documentclass{report}\n\\def\\lectalang{italian}\n\\input{preamble}\n\\begin{document}\n\\include{chapters/01-a}\n\\end{document}\n"
    ch1_old = "\\chapter{A}\nTesto.\n\n\\lectaimage[Schema]{images/a.png}\n\n\\review{rivedere}\n"
    ch1_before = "\\chapter{A}\nTesto.\n"
    ch2 = "\\chapter{B}\nSolo testo.\n"
    h = {k: _put_blob(tmp_path, v.encode()) for k, v in {"main": main_old, "ch1": ch1_old, "ch1_before": ch1_before, "ch2": ch2}.items()}
    override = m.OLD_DEFAULT.replace("max width=0.5\\linewidth,max height=0.22\\textheight", "max width=0.42\\linewidth,max height=0.16\\textheight")
    snapshot = {
        "status": "applied",
        "pre": {"files": {"chapters/01-a.tex": {"blob": h["ch1_before"], "meta": {}}}, "chapters": [], "preamble": override},
        "post": {"files": {"chapters/01-a.tex": h["ch1"], "main.tex": h["main"]}, "chapters": [], "preamble": override},
    }

    async def seed():
        conn = await asyncpg.connect(dsn)
        try:
            uid = await conn.fetchval("INSERT INTO users (username, role) VALUES ('admin', 'admin') RETURNING id")
            cid = await conn.fetchval("INSERT INTO courses (name, slug, owner_id, language, preamble_override) VALUES ('SO', 'so', $1, 'it', $2) RETURNING id",
                                      uid, override)
            chap = await conn.fetchval("INSERT INTO chapters (course_id, position, slug, title, path) VALUES ($1, 1, 'a', 'A', 'chapters/01-a.tex') RETURNING id", cid)
            for path, key, content in [("main.tex", "main", main_old), ("chapters/01-a.tex", "ch1", ch1_old), ("chapters/02-b.tex", "ch2", ch2)]:
                await conn.execute("INSERT INTO project_files (course_id, path, blob, size, is_text, text_content) VALUES ($1, $2, $3, 1, true, $4)",
                                   cid, path, h[key], content)
            await conn.execute("INSERT INTO index_state (chapter_id, blob, chunks) VALUES ($1, $2, 1)", chap, h["ch1"])
            sid = await conn.fetchval("INSERT INTO chat_sessions (course_id, user_id, chapter_id, title) VALUES ($1, $2, $3, '') RETURNING id", cid, uid, chap)
            await conn.execute("INSERT INTO chat_messages (session_id, role, content, status, tokens_in, tokens_out, cost_usd, change) "
                               "VALUES ($1, 'assistant', 'Fatto', 'done', 0, 0, 0, $2::jsonb)", sid, json.dumps(snapshot))
            await conn.execute("INSERT INTO draft_notes (course_id, chapter_id, author_id, kind, src, start, \"end\", body, proposed, status) "
                               "VALUES ($1, $2, $3, 'suggestion', $4, 4, 4, '', $5, 'open')", cid, chap, uid,
                               "\\lectaimage[Schema]{images/a.png}", "\\review{meglio}")
        finally:
            await conn.close()

    asyncio.run(seed())
    upgrade_to("0034")

    files = {r["path"]: r for r in run(dsn, "SELECT path, blob, size, text_content FROM project_files")}
    ch1_text = m.rewrite_text(ch1_old, (0.42, 0.16))
    ch1_new = hashlib.sha256(ch1_text.encode()).hexdigest()
    assert "max width=0.42\\linewidth" in ch1_text and "\\todo[inline]{rivedere}" in ch1_text
    assert files["chapters/01-a.tex"]["blob"] == ch1_new and files["chapters/01-a.tex"]["text_content"] == ch1_text
    assert files["chapters/01-a.tex"]["size"] == len(ch1_text.encode())
    assert (tmp_path / "blobs" / ch1_new[:2] / ch1_new[2:4] / ch1_new).read_text() == ch1_text
    assert "lectalang" not in files["main.tex"]["text_content"]
    assert files["chapters/02-b.tex"]["blob"] == h["ch2"]  # nothing to rewrite
    assert (tmp_path / "blobs" / h["ch1"][:2] / h["ch1"][2:4] / h["ch1"]).exists()  # the old blob stays

    (state,) = run(dsn, "SELECT blob FROM index_state")
    assert state["blob"] == ch1_new  # not re-indexed
    (msg,) = run(dsn, "SELECT change::text AS c FROM chat_messages")
    change = json.loads(msg["c"])
    assert change["post"]["files"]["chapters/01-a.tex"] == ch1_new  # still the current file: «Annulla» is not refused
    assert change["post"]["files"]["main.tex"] == files["main.tex"]["blob"]
    assert change["pre"]["files"]["chapters/01-a.tex"]["blob"] == h["ch1_before"]
    assert change["pre"]["preamble"] is None and change["post"]["preamble"] is None  # like the course's
    (course,) = run(dsn, "SELECT preamble_override FROM courses")
    assert course["preamble_override"] is None
    (note,) = run(dsn, "SELECT src, proposed FROM draft_notes")
    assert note["src"].startswith("\\begin{figure}[H]") and note["proposed"] == "\\todo[inline]{meglio}"
