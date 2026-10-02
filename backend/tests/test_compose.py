"""Writing the study text: how notes and material are split into parts, and how files are grouped."""

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
    def __init__(self, key, file, kind, folder=None, position=0):
        self.key, self.kind, self.position, self.source_file_id = key, kind, position, hash(file)
        self.meta = {"file": file, "folder": folder, "notes": compose.is_notes_name(file) if kind in ("markdown", "text") else False}


def _groups(items):
    return [sorted(g["item_keys"]) for g in ingest.make_groups(items)]


def test_one_notes_file_at_the_root_takes_everything():
    items = [It("a", "slide.pdf", "page", position=1), It("b", "altre.pdf", "page", position=2), It("c", "appunti.md", "markdown", position=3)]
    groups = ingest.make_groups(items)
    assert len(groups) == 1 and groups[0]["notes"] == ["c"]


def test_several_notes_files_take_the_pdfs_with_matching_names():
    items = [
        It("p3", "Lezione 3.pdf", "page", position=1), It("p4", "Lezione 4 - Bode.pdf", "page", position=2),
        It("n3", "appunti lezione 3.md", "markdown", position=3), It("n4", "lezione 4.txt", "text", position=4),
        It("x", "esercizi.pdf", "page", position=5), It("t", "formule.tex", "text", position=6),
    ]
    assert _groups(items) == [["n3", "p3"], ["n4", "p4"], ["x"], ["t"]]


def test_without_notes_each_pdf_is_its_own_group_and_images_in_img_join_their_folder():
    items = [It("a", "uno.pdf", "page", position=1), It("b", "due.pdf", "page", position=2), It("f1", "IMG_1.jpg", "photo", position=3),
             It("f2", "IMG_2.jpg", "photo", position=4)]
    assert _groups(items) == [["a"], ["b"], ["f1", "f2"]]
    items = [It("s", "slide.pdf", "page", folder="L1", position=1), It("i", "x.png", "photo", folder="L1/img", position=2),
             It("n", "note.md", "markdown", folder="L1", position=3)]
    groups = ingest.make_groups(items)
    assert len(groups) == 1 and groups[0]["notes"] == ["n"]


def test_what_counts_as_notes():
    assert compose.is_notes_name("appunti.md") and compose.is_notes_name("Lezione.TXT") and compose.is_notes_name("README")
    assert not compose.is_notes_name("formule.tex") and not compose.is_notes_name("slide.pdf")
