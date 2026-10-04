"""Writing the study text: how notes and material are split into parts, and a lesson is one group."""

from __future__ import annotations

from app.pipeline import compose, ingest


def test_notes_are_split_at_headings():
    notes = "# A\n" + "- punto\n" * 40 + "\n# B\n" + "- altro\n" * 40 + "\n# C\n" + "- ancora\n" * 40
    pieces = compose.split_notes(notes, 3)
    assert len(pieces) == 3 and "".join(pieces) == notes
    assert [p.split("\n", 1)[0] for p in pieces] == ["# A", "# B", "# C"]


def test_latex_is_never_cut_inside_an_environment():
    body = "\\section{Uno}\n" + "Testo.\n\n" * 5 + "\\begin{itemize}\n" + "\\item x\n\n" * 30 + "\\end{itemize}\n" + "Fine.\n\n" * 5
    for n in (2, 3):
        for piece in compose.split_latex(body, n):
            assert piece.count("\\begin{itemize}") == piece.count("\\end{itemize}")


def test_parts_follow_the_notes_when_there_are_any():
    material = "\\section{S}\n" + "Materiale delle slide.\n\n" * 400
    assert len(compose.plan_parts("", material[:2000], budget_chars=20000)) == 1
    alone = compose.plan_parts("", material, budget_chars=4000)
    assert len(alone) > 1 and all(not p["notes"] for p in alone)
    notes = "# A\n- a\n\n# B\n- b\n" * 200
    with_notes = compose.plan_parts(notes, material, budget_chars=4000)
    assert len(with_notes) > 1 and all(p["notes"] and p["material"] == material for p in with_notes)


def test_only_the_relevant_material_goes_with_a_slice_of_notes():
    words = ["alfa", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet"]
    material = "\n\n".join(f"\\section{{Argomento {n}}}\n" + (f"{w} " * 600) for n, w in enumerate(words))
    picked = compose.relevant_material(material, "- appunti su delta e hotel", limit=20000)
    assert "Argomento 3" in picked and "Argomento 7" in picked and len(picked) <= 20000 < len(material)
    assert picked.index("Argomento 3") < picked.index("Argomento 7")


class It:
    def __init__(self, key, file, kind, position=0, lesson="Lezione 4"):
        self.key, self.kind, self.position, self.source_file_id = key, kind, position, hash(file)
        self.meta = {"file": file, "lesson": True, "lesson_title": lesson,
                     "notes": compose.is_notes_name(file) if kind in ("markdown", "text") else False}


def test_a_lesson_is_one_group_with_its_notes():
    items = [It("p", "slide.pdf", "page", position=1), It("n", "lezione-4-appunti.md", "markdown", position=2),
             It("h", "pagina-004.jpg", "handwritten", position=3), It("s", "slide.pdf", "skipped", position=4)]
    groups = ingest.make_groups(items)
    assert len(groups) == 1 and groups[0]["item_keys"] == ["p", "n", "h"] and groups[0]["notes"] == ["n"]
    assert groups[0]["hint"] == "Lezione 4"
    assert ingest.make_groups([]) == []


def test_what_counts_as_notes():
    assert compose.is_notes_name("appunti.md") and compose.is_notes_name("Lezione.TXT") and compose.is_notes_name("README")
    assert not compose.is_notes_name("formule.tex") and not compose.is_notes_name("slide.pdf")
