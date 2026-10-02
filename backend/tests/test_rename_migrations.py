"""Data migrations that carry an existing installation over to the Lecta name.

Each test builds a scratch database at the revision before the migration, seeds it the way a
live installation looks, then upgrades it.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import asyncpg
import pytest

import app.config as cfg

ALEMBIC_INI = str(Path(__file__).parent.parent / "alembic.ini")


@pytest.fixture
def scratch_db(monkeypatch):
    """A fresh database upgraded to `revision`; yields (dsn, upgrade_to)."""
    from alembic import command
    from alembic.config import Config as AlembicConfig

    admin = cfg.Config.database_url_override.replace("postgresql+asyncpg://", "postgresql://")
    base, _, _ = admin.rpartition("/")
    name = f"lecta_mig_{uuid.uuid4().hex[:8]}"

    async def admin_exec(sql: str) -> None:
        conn = await asyncpg.connect(admin)
        try:
            await conn.execute(sql)
        finally:
            await conn.close()

    asyncio.run(admin_exec(f'CREATE DATABASE "{name}"'))
    monkeypatch.setattr(cfg.Config, "database_url_override", f"{base.replace('postgresql://', 'postgresql+asyncpg://')}/{name}")

    def upgrade_to(revision: str) -> None:
        command.upgrade(AlembicConfig(ALEMBIC_INI), revision)

    yield f"{base}/{name}", upgrade_to
    asyncio.run(admin_exec(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def run(dsn: str, sql: str, *args):
    async def go():
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetch(sql, *args)
        finally:
            await conn.close()

    return asyncio.run(go())


def test_site_title_default_follows_the_new_name(scratch_db):
    dsn, upgrade_to = scratch_db
    upgrade_to("0015")
    run(dsn, "INSERT INTO settings (key, value) VALUES ('site', $1::jsonb)",
        json.dumps({"title": "Appunti", "description": "Appunti universitari"}))
    upgrade_to("0016")
    (row,) = run(dsn, "SELECT value FROM settings WHERE key = 'site'")
    value = json.loads(row["value"])
    assert value["title"] == "Lecta"
    assert value["description"] == "Appunti universitari"


def test_a_site_title_the_admin_chose_is_left_alone(scratch_db):
    dsn, upgrade_to = scratch_db
    upgrade_to("0015")
    run(dsn, "INSERT INTO settings (key, value) VALUES ('site', $1::jsonb)", json.dumps({"title": "Il mio studio"}))
    upgrade_to("0016")
    (row,) = run(dsn, "SELECT value FROM settings WHERE key = 'site'")
    assert json.loads(row["value"])["title"] == "Il mio studio"


# --------------------------------------------------------------------------- 0017: LaTeX names

import hashlib  # noqa: E402
import importlib.util  # noqa: E402


def _migration_0017():
    path = Path(__file__).parent.parent / "alembic" / "versions" / "0017_lecta_latex_names.py"
    spec = importlib.util.spec_from_file_location("mig0017", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_rewrite_touches_macros_and_markers_only():
    rewrite = _migration_0017().rewrite
    cases = {
        r"\appuntiimage[Schema]{images/a.png}": r"\lectaimage[Schema]{images/a.png}",
        r"\appuntiimagewithtext[c]{i}{t}": r"\lectaimagewithtext[c]{i}{t}",
        r"\appuntiimagepair{a}{b}{c}{d}": r"\lectaimagepair{a}{b}{c}{d}",
        r"\appuntifigure{f1}": r"\lectafigure{f1}",
        r"\def\appuntilang{italian}": r"\def\lectalang{italian}",
        r"\ifdefined\appuntipublish": r"\ifdefined\lectapublish",
        r"\csname appunti@name@english@#1\endcsname": r"\csname lecta@name@english@#1\endcsname",
        r"\PackageWarning{appunti}{image not found}": r"\PackageWarning{lecta}{image not found}",
        "% appunti:chapters:begin (managed automatically)": "% lecta:chapters:begin (managed automatically)",
        "% ==== Appunti: lezione 12 «Campionamento» (30/09/2026)": "% ==== Lecta: lezione 12 «Campionamento» (30/09/2026)",
        "% ==== Appunti: fine lezione 12": "% ==== Lecta: fine lezione 12",
        "% ---- Appunti: aggiunto da «foto» (30/09)": "% ---- Lecta: aggiunto da «foto» (30/09)",
    }
    for old, new in cases.items():
        assert rewrite(old) == new, old
        assert rewrite(new) == new  # idempotent
    untouched = [
        "Appunti di Fisica", "i miei appunti.jpg", "concorrenza-e-threads-appunti.md", r"\appuntix", r"\appunti",
        "Gli appunti: una raccolta", "% Appunti: nota dell'autore",
    ]
    for text in untouched:
        assert rewrite(text) == text, text


def _put_blob(root: Path, data: bytes) -> str:
    h = hashlib.sha256(data).hexdigest()
    p = root / "blobs" / h[:2] / h[2:4] / h
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return h


def test_stored_projects_snapshots_and_preambles_follow_the_new_names(scratch_db, tmp_path, monkeypatch):
    monkeypatch.setattr(cfg.Config, "data_dir", tmp_path)
    dsn, upgrade_to = scratch_db
    upgrade_to("0016")

    main_old = "\\def\\appuntilang{italian}\n% appunti:chapters:begin (managed automatically, do not edit between these lines)\n\\include{chapters/01-a}\n% appunti:chapters:end\n"
    ch1_old = ("\\chapter{Campionamento}\n% ==== Appunti: lezione 3 «T» (01/10/2026)\nGli appunti dicono molto.\n"
               "\\appuntiimage[Schema]{images/a.png}\n% ==== Appunti: fine lezione 3\n")
    ch1_before = "\\chapter{Campionamento}\n"  # what the chapter looked like before the assistant's answer
    ch2 = "\\chapter{Senza macro}\nSolo testo sugli appunti.\n"
    png = b"\x89PNG\r\n\x1a\n\xff\xfe binary"
    h = {k: _put_blob(tmp_path, v.encode() if isinstance(v, str) else v)
         for k, v in {"main": main_old, "ch1": ch1_old, "ch1_before": ch1_before, "ch2": ch2, "png": png}.items()}
    preamble_old = "\\newcommand{\\appuntiimage}[2][]{\\includegraphics{#2}}\n"
    snapshot = {
        "status": "applied",
        "pre": {"files": {"main.tex": {"blob": h["main"], "meta": {}}, "chapters/01-a.tex": {"blob": h["ch1_before"], "meta": {}},
                          "images/a.png": {"blob": h["png"], "meta": {}}}, "chapters": [], "preamble": preamble_old},
        "post": {"files": {"main.tex": h["main"], "chapters/01-a.tex": h["ch1"], "images/a.png": h["png"]}, "chapters": [],
                 "preamble": preamble_old},
    }

    async def seed():
        conn = await asyncpg.connect(dsn)
        try:
            cid = await conn.fetchval("INSERT INTO courses (name, slug, preamble_override) VALUES ('Appunti di Fisica', 'fisica', $1) RETURNING id", preamble_old)
            chap = await conn.fetchval("INSERT INTO chapters (course_id, position, slug, title, path) VALUES ($1, 1, 'a', 'Campionamento', 'chapters/01-a.tex') RETURNING id", cid)
            for path, key, content in [("main.tex", "main", main_old), ("chapters/01-a.tex", "ch1", ch1_old),
                                       ("chapters/02-b.tex", "ch2", ch2), ("images/a.png", "png", None)]:
                await conn.execute("INSERT INTO project_files (course_id, path, blob, size, is_text, text_content) VALUES ($1, $2, $3, 1, $4, $5)",
                                   cid, path, h[key], content is not None, content)
            await conn.execute("INSERT INTO index_state (chapter_id, blob, chunks) VALUES ($1, $2, 1)", chap, h["ch1"])
            sid = await conn.fetchval("INSERT INTO chat_sessions (course_id, chapter_id, title) VALUES ($1, $2, '') RETURNING id", cid, chap)
            await conn.execute("INSERT INTO chat_messages (session_id, role, content, status, tokens_in, tokens_out, cost_usd, change) VALUES ($1, 'assistant', 'Ho usato appunti.jpg', 'done', 0, 0, 0, $2::jsonb)", sid, json.dumps(snapshot))
            await conn.execute("INSERT INTO settings (key, value) VALUES ('template', $1::jsonb)", json.dumps({"preamble": "\\appuntifigure{x}", "title": "Appunti"}))
        finally:
            await conn.close()

    asyncio.run(seed())
    upgrade_to("0017")

    files = {r["path"]: r for r in run(dsn, "SELECT path, blob, text_content FROM project_files")}
    ch1_new_text = ch1_old.replace("\\appuntiimage", "\\lectaimage").replace("Appunti: ", "Lecta: ")
    ch1_new = hashlib.sha256(ch1_new_text.encode()).hexdigest()
    main_new = hashlib.sha256(main_old.replace("\\appuntilang", "\\lectalang").replace("appunti:chapters", "lecta:chapters").encode()).hexdigest()
    assert files["chapters/01-a.tex"]["blob"] == ch1_new
    assert (tmp_path / "blobs" / ch1_new[:2] / ch1_new[2:4] / ch1_new).read_text() == ch1_new_text
    assert files["chapters/01-a.tex"]["text_content"] == ch1_new_text and "Gli appunti dicono molto." in ch1_new_text
    assert files["main.tex"]["blob"] == main_new
    assert files["chapters/02-b.tex"]["blob"] == h["ch2"] and files["images/a.png"]["blob"] == h["png"]  # nothing to rewrite
    assert (tmp_path / "blobs" / h["ch1"][:2] / h["ch1"][2:4] / h["ch1"]).exists()  # the old blob stays for a rollback

    (state,) = run(dsn, "SELECT blob FROM index_state")
    assert state["blob"] == ch1_new  # the chapter is not re-indexed

    (msg,) = run(dsn, "SELECT change::text AS c FROM chat_messages")
    change = json.loads(msg["c"])
    assert change["post"]["files"]["chapters/01-a.tex"] == ch1_new  # still equal to the current file: «Annulla» is not refused
    assert change["post"]["files"]["main.tex"] == main_new == change["pre"]["files"]["main.tex"]["blob"]
    assert change["pre"]["files"]["chapters/01-a.tex"]["blob"] == h["ch1_before"]  # an unchanged file keeps its hash
    assert change["pre"]["preamble"] == "\\newcommand{\\lectaimage}[2][]{\\includegraphics{#2}}\n"

    (course,) = run(dsn, "SELECT name, preamble_override FROM courses")
    assert course["name"] == "Appunti di Fisica" and "\\lectaimage" in course["preamble_override"]
    (msg_text,) = run(dsn, "SELECT content FROM chat_messages")
    assert msg_text["content"] == "Ho usato appunti.jpg"
    (template,) = run(dsn, "SELECT value::text AS v FROM settings WHERE key = 'template'")
    assert json.loads(template["v"]) == {"preamble": "\\lectafigure{x}", "title": "Appunti"}
