from __future__ import annotations


from app.services import projects


async def test_course_crud_and_project_layout(admin, db):
    r = await admin.post(
        "/api/courses",
        json={"name": "Analisi Matematica I", "academic_year": "2025/26", "language": "it", "chapters": ["Limiti", "Derivate"]},
    )
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["slug"] == "analisi-matematica-i"
    assert [ch["path"] for ch in c["chapters"]] == ["chapters/01-limiti.tex", "chapters/02-derivate.tex"]

    main = await projects.read_text(db, c["id"], "main.tex")
    assert "\\include{chapters/01-limiti}" in main and "\\include{chapters/02-derivate}" in main
    assert "\\input{preamble}" in main and "[italian]{babel}" in main

    # Add, rename, reorder, delete chapters; main.tex follows.
    r = await admin.post(f"/api/courses/{c['id']}/chapters", json={"title": "Integrali", "position": 1})
    integrali = r.json()
    assert integrali["path"] == "chapters/01-integrali.tex"
    detail = (await admin.get(f"/api/courses/{c['id']}")).json()
    assert [ch["title"] for ch in detail["chapters"]] == ["Integrali", "Limiti", "Derivate"]
    ids = [ch["id"] for ch in detail["chapters"]]
    r = await admin.post(f"/api/courses/{c['id']}/chapters/reorder", json={"ids": [ids[1], ids[2], ids[0]]})
    assert [ch["path"] for ch in r.json()] == [
        "chapters/01-limiti.tex",
        "chapters/02-derivate.tex",
        "chapters/03-integrali.tex",
    ]
    await db.rollback()
    main = await projects.read_text(db, c["id"], "main.tex")
    assert main.index("01-limiti") < main.index("02-derivate") < main.index("03-integrali")

    r = await admin.patch(f"/api/courses/{c['id']}/chapters/{ids[0]}", json={"title": "Integrali indefiniti"})
    assert r.status_code == 200
    await db.rollback()
    src = await projects.read_text(db, c["id"], "chapters/03-integrali.tex")
    assert "\\chapter{Integrali indefiniti}" in src

    r = await admin.delete(f"/api/courses/{c['id']}/chapters/{ids[1]}")
    assert r.status_code == 200
    await db.rollback()
    main = await projects.read_text(db, c["id"], "main.tex")
    assert "limiti" not in main and "01-derivate" in main

    tree = (await admin.get("/api/tree")).json()
    assert any(t["id"] == c["id"] and len(t["chapters"]) == 2 for t in tree)

    r = await admin.patch(f"/api/courses/{c['id']}", json={"language": "en", "engine": "lualatex", "tags": ["math"]})
    assert r.json()["language"] == "en" and r.json()["engine"] == "lualatex"

    assert (await admin.post(f"/api/courses/{c['id']}/delete", json={"confirm_slug": "nope"})).status_code == 400
    assert (await admin.post(f"/api/courses/{c['id']}/delete", json={"confirm_slug": c["slug"]})).status_code == 200
    assert (await admin.get(f"/api/courses/{c['id']}")).status_code == 404


async def test_slugs_are_unique(admin):
    a = (await admin.post("/api/courses", json={"name": "Fisica"})).json()
    b = (await admin.post("/api/courses", json={"name": "Fisica"})).json()
    assert a["slug"] == "fisica" and b["slug"] == "fisica-2"


def test_path_validation():
    import pytest

    ok = ["main.tex", "chapters/01-x.tex", "figures/fig-1.tex", "images/a.png"]
    for p in ok:
        assert projects.validate_path(p) == p
    bad = [
        "../x.tex", "/etc/passwd.tex", "a/../../b.tex", ".latexmkrc", "latexmkrc", "chapters/.hidden.tex",
        "preamble.tex", "figures-cache/x.pdf", "_root.tex", "x.sh", "a/b/c/d/e.tex", "x\x00.tex", "",
    ]
    for p in bad:
        with pytest.raises(Exception):
            projects.validate_path(p)


async def test_dashboard(admin):
    d = (await admin.get("/api/dashboard")).json()
    assert {"recent_documents", "recent_courses", "jobs"} <= set(d) and "inbox_count" not in d
