from __future__ import annotations

from app.services import concepts


async def _course(admin, name: str, chapters: dict[str, str]) -> dict:
    r = await admin.post("/api/courses", json={"name": name, "language": "it", "chapters": list(chapters)})
    assert r.status_code == 201, r.text
    c = r.json()
    for ch, body in zip(c["chapters"], chapters.values()):
        cur = (await admin.get(f"/api/courses/{c['id']}/files/content", params={"path": ch["path"]})).json()
        content = f"\\chapter{{{ch['title']}}}\n\n{body}"
        r = await admin.put(f"/api/courses/{c['id']}/files/content", json={"path": ch["path"], "content": content, "base_blob": cur.get("blob")})
        assert r.status_code == 200, r.text
    return c


def test_title_normalisation_and_matching():
    t = concepts.title_tokens
    assert t("La \\emph{Discesa} del Gradiente") == t("discesa del gradiente")
    assert t("Calcolo di $\\delta$ e \\(x^2\\)") == t("Calcolo")
    assert t("Probabilità") == t("probabilita")
    assert concepts.lexical_score(t("Discesa del gradiente"), t("Discesa del gradiente")) == 1.0
    assert concepts.lexical_score(t("Massima verosimiglianza e loss"), t("Massima verosimiglianza")) is not None
    assert concepts.lexical_score(t("Ottimizzazione"), t("Ottimizzazione convessa")) is None  # one word only
    assert concepts.lexical_score(t("Metodi del gradiente"), t("Discesa del gradiente")) is None


def test_parse_units_numbering_and_lines():
    src = "\\chapter{Ottimizzazione}\n% \\section{Commentata}\n\\section{Discesa del gradiente}\ntesto\n\\subsection{SGD e momentum}\n\\section*{Note}\n\\section{Adam}\n"
    units = concepts.parse_units(1, 10, 4, "Ottimizzazione", "chapters/04-ottimizzazione.tex", src)
    got = [(u.level, u.title, u.number, u.line) for u in units]
    assert got == [
        ("chapter", "Ottimizzazione", "4", 1),
        ("section", "Discesa del gradiente", "4.1", 3),
        ("subsection", "SGD e momentum", "4.1.1", 5),
        ("section", "Adam", "4.2", 7),  # "Note" is generic and dropped
    ]
    assert units[1].chunk_heading == "Ottimizzazione / Discesa del gradiente"


async def test_concepts_lexical_across_courses(admin):
    a = await _course(
        admin,
        "Reti Neurali Profonde",
        {
            "Introduzione": "\\section{Notazione}\nx\n",
            "Ottimizzazione stocastica": "\\section{La discesa del Gradiente}\nx\n\\subsection{Momento di Nesterov}\nx\n",
            "Regolarizzazione": "\\section{Massima verosimiglianza e perdita}\nx\n\\section{Tecniche di pooling spaziale}\nx\n",
            "Architetture": "\\section{Tecniche di pooling spaziale}\nx\n",
        },
    )
    b = await _course(
        admin,
        "Ottimizzazione Numerica",
        {
            "Introduzione": "\\section{Notazione}\nx\n",
            "Metodi iterativi": "\\section{Discesa del gradiente}\nx\n",
            "Statistica": "\\section{Massima verosimiglianza}\nx\n",
        },
    )
    c = await _course(admin, "Letteratura Latina", {"Autori": "\\section{Cicerone oratore}\nx\n"})

    r = await admin.get("/api/concepts")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["mode"] in ("lexical", "semantic")
    ids = {a["id"], b["id"], c["id"]}
    mine = [k for k in data["concepts"] if set(k["course_ids"]) & ids]
    labels = {k["label"]: k for k in mine}
    assert set(labels) == {"Discesa del gradiente", "Massima verosimiglianza"}, labels.keys()

    grad = labels["Discesa del gradiente"]
    assert grad["course_ids"] == sorted([a["id"], b["id"]])
    assert isinstance(grad["id"], str) and grad["score"] == 1.0
    occ = {o["course_id"]: o for o in grad["occurrences"]}
    assert occ[a["id"]]["section"] == "La discesa del Gradiente"
    assert occ[a["id"]]["section_number"] == "2.1" and occ[a["id"]]["chapter_number"] == 2
    assert occ[a["id"]]["chapter_title"] == "Ottimizzazione stocastica"
    assert occ[a["id"]]["path"] == "chapters/02-ottimizzazione-stocastica.tex" and occ[a["id"]]["line"] == 3
    assert occ[b["id"]]["course_name"] == "Ottimizzazione Numerica"
    assert set(occ[b["id"]]) >= {"course_id", "course_name", "chapter_id", "chapter_title", "chapter_number", "section", "section_number", "path", "line"}

    # "Tecniche di pooling spaziale" appears twice in the same course only: no self-match.
    assert not any("pooling" in k["label"].lower() for k in data["concepts"])

    courses = {x["id"]: x for x in data["courses"]}
    assert courses[a["id"]]["concept_count"] == 2 and courses[b["id"]]["concept_count"] == 2
    assert courses[c["id"]]["concept_count"] == 0  # courses without concepts are listed too
    assert set(courses[c["id"]]) == {"id", "name", "slug", "status", "published", "concept_count"}
    assert courses[c["id"]]["status"] in ("ok", "working", "error")

    # Stable ids, and the map follows edits.
    again = (await admin.get("/api/concepts")).json()
    assert [k["id"] for k in again["concepts"]] == [k["id"] for k in data["concepts"]]
    ch = b["chapters"][1]
    cur = (await admin.get(f"/api/courses/{b['id']}/files/content", params={"path": ch["path"]})).json()
    await admin.put(
        f"/api/courses/{b['id']}/files/content",
        json={"path": ch["path"], "content": "\\chapter{Metodi iterativi}\n\\section{Metodo di Newton}\n", "base_blob": cur["blob"]},
    )
    after = (await admin.get("/api/concepts")).json()
    assert not any(k["label"] == "Discesa del gradiente" and b["id"] in k["course_ids"] for k in after["concepts"])


async def test_concepts_admin_only(anon):
    r = await anon.get("/api/concepts")
    assert r.status_code == 404
