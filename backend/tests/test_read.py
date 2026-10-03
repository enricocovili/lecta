"""Reading: reply parsing and stitching of unit bodies."""

from __future__ import annotations

from app.pipeline import read


def test_reply_is_parsed():
    r = read.parse_reply(
        "%%TITLE: Convoluzione\n%%FIGURE p3n1 3 0.1 0.2 0.8 0.9\n%%DROP f1p3i2\n\\section{Convoluzione}\n$y = x * h$\n%%END\nignored"
    )
    assert r["title"] == "Convoluzione" and r["complete"]
    assert r["figures"] == [{"id": "p3n1", "page": 3, "bbox": [0.1, 0.2, 0.8, 0.9]}]
    assert r["drops"] == ["f1p3i2"] and r["body"] == "\\section{Convoluzione}\n$y = x * h$"
    fenced = read.parse_reply("```latex\nciao\n```")
    assert fenced["body"] == "ciao" and not fenced["complete"]


def test_stitch_resolves_pictures_and_repeated_sections():
    parts = [
        {"unit": "u1", "body": "\\section{Segnali}\nPrimo. \\lectaimage[A]{k1}\n\\lectaimage{sconosciuta}\n\\includegraphics{x.png}", "drops": []},
        {"unit": "u2", "body": "\\section{Segnali}\nContinua. \\lectaimage{k1}\n\\begin{tikzpicture}\\draw (0,0);\\end{tikzpicture}", "drops": ["k3"]},
    ]
    known = {"k1": "u1", "k2": "u2", "k3": "u2"}
    body, status = read.stitch(parts, known, "Capitolo")
    assert body.count("\\section{Segnali}") == 1  # the second unit continues the same section
    assert body.count("{k1}") == 1 and "sconosciuta" not in body
    assert "\\includegraphics" not in body and "tikzpicture" not in body
    assert "\\lectaimage{k2}" in body  # not placed by the model: appended at the end of its unit
    assert status == {"k1": "used", "k2": "appended", "k3": "dropped"}


def test_stitch_keeps_the_text_of_an_image_beside_its_text():
    parts = [
        {"unit": "u1", "body": "\\lectaimagewithtext[A]{k1}{Spiega A.}\n\\lectaimagewithtext[A di nuovo]{k1}{Spiega ancora.}\n"
                               "\\lectaimagewithtext{ignota}{Spiega B.}\n\\lectaimagepair{Uno}{k1}{Due}{k2}\n", "drops": []},
    ]
    body, status = read.stitch(parts, {"k1": "u1", "k2": "u1"}, "Capitolo")
    assert body.count("{k1}") == 1 and "Spiega ancora." in body and "Spiega B." in body
    assert "ignota" not in body and "\\lectaimagewithtext[A di nuovo]{}{Spiega ancora.}" in body
    # The pair: k1 was already placed, so its slot is emptied and the other picture stays.
    assert "\\lectaimagepair{Uno}{}{Due}{k2}" in body
    assert status == {"k1": "used", "k2": "used"}


def test_picture_commands_are_parsed_in_one_place():
    from app.services import latexmacros as lm

    src = "x \\lectaimage[c]{a} y \\lectaimagewithtext[t]{b}{testo {annidato} qui} z \\lectaimagepair{1}{c}{2}{d}"
    assert lm.image_names(src) == ["a", "b", "c", "d"]
    assert lm.map_images(src, lambda k: "images/" + k + ".png") == (
        "x \\lectaimage[c]{images/a.png} y \\lectaimagewithtext[t]{images/b.png}{testo {annidato} qui} z "
        "\\lectaimagepair{1}{images/c.png}{2}{images/d.png}"
    )
    assert lm.without_images(src).split() == ["x", "c", "y", "t", "testo", "{annidato}", "qui", "z", "1", "2"]


def test_units_respect_limits():
    class It:
        def __init__(self, key, kind="page", text="x" * 100, sf=1, image=None):
            self.key, self.kind, self.text, self.source_file_id, self.image_blob = key, kind, text, sf, image

    items = [It(f"i{n}", image="b" if n % 2 else None) for n in range(12)] + [It("m", kind="markdown"), It("ph", kind="photo")]
    units = read.build_units(items, max_pages=5, max_chars=10_000)
    kinds = [u["kind"] for u in units]
    assert kinds[-2:] == ["latex", "photo"]
    pages = [u for u in units if u["kind"] == "pages"]
    assert sum(len(u["items"]) for u in pages) == 12 and all(len(u["items"]) <= 5 for u in pages)
    assert all(sum(1 for k in u["items"] if int(k[1:]) % 2) <= read.MAX_IMAGES_PER_UNIT for u in pages)
