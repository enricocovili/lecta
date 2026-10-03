"""Draft: a chapter typeset by the real LaTeX, block by block, and shown as SVG pictures.

The chapter is split into blocks (services/blocks). One engine run of a wrapper document (the course's own
preamble, then every block from a file of its own, each starting on a new page) typesets what is not cached yet;
mutool turns every page into SVG and gives its ink box, which the picture is cropped to.

What a block looks like depends on its text and on the state around it: the counters (chapter, section, theorem,
equation…), the labels it refers to, the pictures it includes and the preamble. So a typeset block is cached as a
*variant*: its text and inputs (the content key) plus the counter state it started from, with the state it left
behind. Walking a chapter in Python from its first state, block after block, tells whether every block has a variant
for the state it would start from; then nothing is typeset. Otherwise the wrapper does the same walk in TeX, with
the real counters: a block whose cached start state matches is skipped (its end state is set and nothing is
typeset), anything else is typeset. A *neutral* block (it changes no counter and prints none: most paragraphs)
fits any state. So an edit typesets the block that changed, plus the numbered blocks after it that it renumbers.

Cache, per course (in the data dir): `v/<variant>.json` + `v/<variant>-<n>.svg`, `index.json` (content key →
recent variants), `meta-<preamble hash>.json` (the state at the start of the document, the text area), `labels.json`
(the `\\newlabel` lines seen, given to every run so references resolve in one pass).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import config
from ..models import Chapter, Course
from . import blobs, latex, latexmacros, projects, tables, templates
from .blocks import Block, split
from .settings import get_section
from .texttools import latex_to_text

PT_TO_BP = 72 / 72.27
PAD = 1.5  # around a picture, in bp
MAX_VARIANTS = 3  # kept per content key
PRUNE_ABOVE = 4000  # files in a course's cache before stale variants are removed
PRUNE_AGE_S = 30 * 86400

_LEVELS = {"section": 1, "subsection": 2, "subsubsection": 3, "paragraph": 4}
_HEADING_RE = re.compile(r"\\(part|chapter|section|subsection|subsubsection|paragraph)\*?\s*(?:\[[^\]]*\])?\s*\{")
_REF_RE = re.compile(r"\\(?:ref|eqref|pageref|autoref|cref|Cref|nameref|vref)\*?\s*\{([^{}]*)\}")
_FILE_RE = re.compile(r"images/[A-Za-z0-9._+/-]+")
_FIGURE_RE = re.compile(r"\\lectafigure\s*(?:\[[^\]]*\])?\s*\{([^{}]+)\}")
_NEWLABEL_RE = re.compile(r"^\\newlabel\{([^{}]+)\}")
_ERROR_RE = re.compile(r"^\./_b\d+/(\d+)\.tex:(\d+): (.*)$", re.M)
_ANY_ERROR_RE = re.compile(r"^(?:\./)?([^:\n]+\.tex):(\d+): (.*)$", re.M)
_SVG_TAG_RE = re.compile(r"<svg\b[^>]*>")
_SVG_NAME_RE = re.compile(r"^[0-9a-f]{32}-\d{1,4}\.svg$")
_BBOX_RE = re.compile(r'<page bbox="([-0-9.e ]+)"')
# Prints a counter: such a block depends on the state even when it changes none.
_PRINTS_COUNTER_RE = re.compile(r"\\(?:the[A-Za-z@]+|arabic|roman|Roman|alph|Alph|fnsymbol|value|setcounter|addtocounter|stepcounter|refstepcounter)(?![A-Za-z])")

# Before \documentclass: remember every counter that gets defined (the kernel's own are listed by hand).
WRAP_HEAD = r"""\makeatletter
\gdef\lecta@counters{\do{equation}\do{enumi}\do{enumii}\do{enumiii}\do{enumiv}\do{footnote}\do{mpfootnote}}
\let\lecta@defcounter\@definecounter
\def\@definecounter#1{\lecta@defcounter{#1}\g@addto@macro\lecta@counters{\do{#1}}}
\makeatother
"""

# After \begin{document}: no page styles (the pictures are cropped to the ink), one-sided, and the block protocol.
# \lectablock{n}{cached start state}{setters}: skip the block when the counters are where its cached picture started
# (and set them to where it ended), or typeset _b<chapter>/n.tex on a page of its own. \lectaskip{n}: a cached
# neutral block, skipped whatever the state. The .lecta file records it:
# B <state> (start of the document), G <text left> <text width>, S n <first page> <state>, E n <state>, K n, D (done).
WRAP_BODY = r"""\makeatletter
\@twosidefalse\@mparswitchfalse
\pagestyle{empty}\let\ps@plain\ps@empty\let\ps@headings\ps@empty\let\ps@myheadings\ps@empty
\renewcommand\pagestyle[1]{}\renewcommand\thispagestyle[1]{}
\newwrite\lecta@out\immediate\openout\lecta@out=\jobname.lecta
\def\lecta@pagename{page}
\def\lecta@mkstate{\gdef\lecta@st{}%
  \def\do##1{\def\lecta@n{##1}\ifx\lecta@n\lecta@pagename\else\xdef\lecta@st{\lecta@st##1=\the\csname c@##1\endcsname;}\fi}%
  \lecta@counters\xdef\lecta@st{\detokenize\expandafter{\lecta@st}}}
\def\lectaset#1#2{\global\csname c@#1\endcsname=#2\relax}
\def\lectablock#1#2#3{\lecta@mkstate\edef\lecta@want{\detokenize{#2}}%
  \ifx\lecta@st\lecta@want\expandafter\@firstoftwo\else\expandafter\@secondoftwo\fi
  {#3\immediate\write\lecta@out{K #1}}%
  {\clearpage\immediate\write\lecta@out{S #1 \the\numexpr\ReadonlyShipoutCounter+1\relax\space\lecta@st}%
   \input{\lecta@dir/#1}\par\lecta@mkstate\immediate\write\lecta@out{E #1 \lecta@st}}}
\def\lectaskip#1{\immediate\write\lecta@out{K #1}}
\def\lectadone{\clearpage\immediate\write\lecta@out{D}}
\lecta@mkstate\immediate\write\lecta@out{B \lecta@st}
\immediate\write\lecta@out{G \the\dimexpr1in+\hoffset+\oddsidemargin\relax\space\the\textwidth}
\makeatother
"""

_locks: dict[Any, asyncio.Lock] = defaultdict(asyncio.Lock)


def cache_dir(course_id: int) -> Path:
    return config.data_dir / "draft" / str(course_id)


def svg_path(course_id: int, name: str) -> Path | None:
    """A cached picture by the name in its URL (None if the name is not one of ours)."""
    if not _SVG_NAME_RE.match(name):
        return None
    return cache_dir(course_id) / "v" / name


def _sha(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(hashlib.sha256(p.encode()).digest())
    return h.hexdigest()


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def preamble_of(main_tex: str) -> str:
    """What main.tex says before \\begin{document}: the class, the preamble and the packages after it."""
    i = main_tex.find("\\begin{document}")
    return main_tex[:i] if i >= 0 else main_tex


def _with_chapter(state: str, number: int) -> str:
    return re.sub(r"(^|;)chapter=-?\d+;", lambda m: f"{m.group(1)}chapter={number};", state, count=1)


def _setters(state: str) -> str:
    out = []
    for part in state.split(";"):
        name, _, value = part.rpartition("=")
        if name and re.fullmatch(r"-?\d+", value) and not re.search(r"[\\{}%#\s]", name):
            out.append(f"\\lectaset{{{name}}}{{{value}}}")
    return "".join(out)


def heading_of(src: str) -> tuple[str, int, str] | None:
    """(command, level, title) of a heading block."""
    m = _HEADING_RE.search(src)
    if not m:
        return None
    end = latexmacros.match_brace(src, m.end() - 1)
    if end < 0:
        return None
    title = re.sub(r"\s+", " ", latex_to_text(src[m.end() : end])).strip()
    return m.group(1), _LEVELS.get(m.group(1), 0), title


def _crop(svg: str, box: tuple[float, float, float, float], left: float, width: float) -> tuple[str, dict[str, float]] | None:
    x0, y0, x1, y1 = box
    if x1 <= x0 or y1 <= y0:
        return None
    vx0 = min(left, x0) - PAD
    vx1 = max(left + width, x1) + PAD
    vy0, vy1 = y0 - PAD, y1 + PAD
    w, h = vx1 - vx0, vy1 - vy0
    tag = f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" version="1.1" width="{w:.2f}" height="{h:.2f}" viewBox="{vx0:.2f} {vy0:.2f} {w:.2f} {h:.2f}">'
    cropped, n = _SVG_TAG_RE.subn(tag, svg, count=1)
    if not n:
        return None
    return cropped, {"w": round(w, 2), "h": round(h, 2), "x": round(vx0 - left, 2)}


class Inputs:
    """What every block of a course depends on, besides its own text."""

    def __init__(self, course_id: int, engine: str, main_pre: str, preamble: str, manifest: dict[str, str], labels: dict[str, str]):
        self.course_id = course_id
        self.engine = engine
        self.main_pre = main_pre
        self.preamble = preamble
        self.manifest = manifest
        self.labels = labels
        self.prehash = _sha(engine, main_pre, preamble)

    def content_key(self, b: Block) -> str:
        """The block's text and everything it pulls in: pictures, figures, the labels it refers to."""
        parts = [self.prehash, b.src]
        for ref in sorted(set(_FILE_RE.findall(b.src))):
            parts.append(ref + "=" + self.manifest.get(ref, "-"))
        for name in sorted(set(_FIGURE_RE.findall(b.src))):
            parts.append(name + "=" + self.manifest.get(f"figures/{name.strip()}.tex", "-"))
        for group in _REF_RE.findall(b.src):
            for name in group.split(","):
                parts.append("ref:" + name.strip() + "=" + self.labels.get(name.strip(), "-"))
        return _sha(*parts)


def _parse_marks(marks: str) -> dict[str, Any]:
    out: dict[str, Any] = {"S": {}, "E": {}, "K": set(), "B": None, "G": None, "D": False}
    for line in marks.splitlines():
        kind, _, rest = line.partition(" ")
        if kind == "S":
            n, page, state = (rest.split(" ", 2) + ["", ""])[:3]
            out["S"][int(n)] = (int(page), state)
        elif kind == "E":
            n, _, state = rest.partition(" ")
            out["E"][int(n)] = state
        elif kind == "K":
            out["K"].add(int(rest))
        elif kind == "B":
            out["B"] = rest
        elif kind == "G":
            left, width = rest.split()
            out["G"] = (float(left.removesuffix("pt")) * PT_TO_BP, float(width.removesuffix("pt")) * PT_TO_BP)
        elif kind == "D":
            out["D"] = True
    return out


class ChapterRender:
    def __init__(self, course: Course, chapter: Chapter, source: str, inputs: Inputs):
        self.course = course
        self.chapter = chapter
        self.source = source
        self.inputs = inputs
        self.dir = cache_dir(course.id)
        self.blocks = split(source)
        # What the PDF shows: tables get their rules there too (services/tables), so the draft typesets the same.
        self.texts = [tables.add_rules(b.src) for b in self.blocks]
        self.keys = [inputs.content_key(Block(b.start, b.end, t, b.heading)) for b, t in zip(self.blocks, self.texts)]

    # ------------------------------------------------------------------ cache

    def index(self) -> dict[str, Any]:
        return _read_json(self.dir / "index.json", {})

    def variant(self, vid: str) -> dict[str, Any] | None:
        return _read_json(self.dir / "v" / f"{vid}.json", None)

    def meta(self) -> dict[str, Any] | None:
        return _read_json(self.dir / f"meta-{self.inputs.prehash[:32]}.json", None)

    def walk(self) -> list[dict[str, Any] | None]:
        """The cached variant of every block, following the counters from the chapter's start (None: not cached)."""
        meta = self.meta()
        index = self.index()
        state = _with_chapter(meta["base"], self.chapter.position - 1) if meta else None
        plan: list[dict[str, Any] | None] = []
        for key in self.keys:
            found = None
            for vid in index.get(key, {}).get("v", []):
                v = self.variant(vid)
                if v is not None and (v.get("neutral") or (state is not None and v["start"] == state)):
                    found = v | {"id": vid}
                    break
            plan.append(found)
            if not found:
                state = None
            elif not found.get("neutral"):
                state = found["end"]
        return plan

    # ------------------------------------------------------------------ typesetting

    def candidate(self, i: int, planned: dict[str, Any] | None, index: dict[str, Any]) -> dict[str, Any] | None:
        if planned:
            return planned
        for vid in index.get(self.keys[i], {}).get("v", []):
            v = self.variant(vid)
            if v is not None:
                return v | {"id": vid}
        return None

    def wrapper(self, plan: list[dict[str, Any] | None], index: dict[str, Any]) -> tuple[str, list[dict[str, Any] | None]]:
        cands = [self.candidate(i, p, index) for i, p in enumerate(plan)]
        out = [WRAP_HEAD, self.inputs.main_pre, "\\begin{document}\n", WRAP_BODY]
        out.append(f"\\makeatletter\\def\\lecta@dir{{_b{self.chapter.id}}}\\lectaset{{chapter}}{{{self.chapter.position - 1}}}\\makeatother\n")
        for i, c in enumerate(cands):
            if c and c.get("neutral"):
                out.append(f"\\lectaskip{{{i}}}\n")
                continue
            start = c["start"] if c else ""
            setters = _setters(c["end"]) if c else ""
            out.append(f"\\lectablock{{{i}}}{{{start}}}{{{setters}}}\n")
        out.append("\\lectadone\n\\end{document}\n")
        return "".join(out), cands

    async def typeset(self, db: AsyncSession, plan: list[dict[str, Any] | None]) -> tuple[list[dict[str, Any] | None], list[str], int]:
        wd = projects.work_dir(self.course.id, "blocks")
        job = f"_blk-{self.chapter.id}"
        async with _locks[("materialize", self.course.id)]:
            await projects.materialize(db, self.course, wd)
            index = self.index()
            labels = self.inputs.labels
        doc, cands = self.wrapper(plan, index)
        bdir = wd / f"_b{self.chapter.id}"
        shutil.rmtree(bdir, ignore_errors=True)
        bdir.mkdir(parents=True)
        for i, t in enumerate(self.texts):
            (bdir / f"{i}.tex").write_text(t + "\n")
        (wd / f"{job}.tex").write_text(doc)
        # pdflatex starts from the preamble precompiled once (the compile service makes it the first time).
        fmt = None
        if self.inputs.engine == "pdflatex":
            fmt = f"_fmt-{self.inputs.prehash[:32]}"
            if not (wd / f"{fmt}.tex").is_file():
                (wd / f"{fmt}.tex").write_text(WRAP_HEAD + self.inputs.main_pre + "\\begin{document}\\end{document}\n")
        (wd / f"{job}.aux").write_text("\\relax\n" + "".join(line + "\n" for line in labels.values()))
        lset = await get_section(db, "latex")
        res = await latex.blocks({
            "key": f"course-{self.course.id}", "workdir": latex.rel(wd), "job": job, "engine": self.inputs.engine,
            "figure_cache": latex.rel(projects.work_dir(self.course.id, "figcache")), "timeout": lset.timeout_s, "format": fmt,
        })
        if res.get("status") == "superseded":
            raise Superseded()
        return await asyncio.to_thread(self.collect, wd, job, res, cands)

    def collect(self, wd: Path, job: str, res: dict[str, Any], cands: list[dict[str, Any] | None]) -> tuple[list[dict[str, Any] | None], list[str], int]:
        """The variant of every block after a run (typeset ones are stored), the warnings, how many were typeset."""
        warnings: list[str] = []
        marks = _parse_marks(res.get("marks") or "")
        log = res.get("log") or ""
        errors: dict[int, str] = {}
        for m in _ERROR_RE.finditer(log):
            n, line, msg = int(m.group(1)), int(m.group(2)), m.group(3).strip()
            if 0 <= n < len(self.blocks) and n not in errors:
                errors[n] = f"riga {self.blocks[n].start + line - 1}: {msg}"
        for m in _ANY_ERROR_RE.finditer(log):
            if not m.group(1).startswith("_b") and len(warnings) < 3:
                warnings.append(f"{m.group(1)}:{m.group(2)}: {m.group(3).strip()}")
        if res.get("status") == "timeout":
            warnings.append("La composizione LaTeX ha superato il tempo massimo.")
        elif not marks["D"] and not warnings:
            tail = [ln for ln in log.strip().splitlines() if ln.startswith("!")][-2:] or log.strip().splitlines()[-2:]
            warnings.append("La composizione LaTeX si è fermata: " + " / ".join(tail))
        for f in res.get("figures") or []:
            if f.get("status") in ("error", "timeout"):
                warnings.append(f"La figura {f.get('name')} non si compila.")

        index = self.index()
        if marks["B"] and marks["G"]:
            _write_json(self.dir / f"meta-{self.inputs.prehash[:32]}.json", {"base": marks["B"], "left": marks["G"][0], "width": marks["G"][1]})
        geom = marks["G"] or ((self.meta() or {}).get("left", 72.0), (self.meta() or {}).get("width", 451.0))
        boxes = [tuple(float(x) for x in b.split()) for b in _BBOX_RE.findall(res.get("bbox") or "")]
        svg_dir = config.latex_root / res["svg_dir"] if res.get("svg_dir") else None
        starts = sorted((page, n) for n, (page, _) in marks["S"].items())
        out: list[dict[str, Any] | None] = []
        now = int(time.time())
        for i in range(len(self.blocks)):
            if i in marks["K"] and cands[i]:
                out.append(cands[i])
                entry = index.setdefault(self.keys[i], {"v": []})
                entry["t"] = now
                continue
            if i not in marks["S"] or i not in marks["E"]:
                out.append({"id": None, "pages": [], "error": errors.get(i) or "non composto", "start": None, "end": None})
                continue
            first, start = marks["S"][i]
            later = [p for p, _ in starts if p > first]
            last = (later[0] - 1) if later else len(boxes)
            vid = _sha(self.keys[i], start)[:32]
            pages = []
            for page in range(first, last + 1):
                if page - 1 >= len(boxes) or svg_dir is None:
                    break
                data = projects.safe_read_output(svg_dir / f"{page}.svg", 64 * 1024 * 1024)
                if data is None:
                    continue
                cropped = _crop(data.decode("utf-8", "replace"), boxes[page - 1], *geom)
                if cropped is None:
                    continue
                svg, size = cropped
                k = len(pages)
                (self.dir / "v").mkdir(parents=True, exist_ok=True)
                (self.dir / "v" / f"{vid}-{k}.svg").write_text(svg)
                pages.append(size | {"n": k})
            end = marks["E"][i]
            neutral = start == end and not _PRINTS_COUNTER_RE.search(self.texts[i]) and i not in errors
            v = {"start": start, "end": end, "pages": pages, "error": errors.get(i), "neutral": neutral}
            _write_json(self.dir / "v" / f"{vid}.json", v)
            entry = index.setdefault(self.keys[i], {"v": []})
            entry["v"] = [vid] + [x for x in entry["v"] if x != vid][: MAX_VARIANTS - 1]
            entry["t"] = now
            out.append(v | {"id": vid})
        _write_json(self.dir / "index.json", index)
        self._learn_labels(wd / f"{job}.aux")
        self._prune(index)
        return out, warnings, len(marks["S"])

    def _learn_labels(self, aux: Path) -> None:
        data = projects.safe_read_output(aux, 8 * 1024 * 1024)
        if not data:
            return
        labels = _read_json(self.dir / "labels.json", {})
        for line in data.decode("utf-8", "replace").splitlines():
            m = _NEWLABEL_RE.match(line)
            if m:
                labels[m.group(1)] = line
        _write_json(self.dir / "labels.json", labels)

    def _prune(self, index: dict[str, Any]) -> None:
        vdir = self.dir / "v"
        try:
            files = list(vdir.iterdir())
        except OSError:
            return
        if len(files) <= PRUNE_ABOVE:
            return
        cutoff = time.time() - PRUNE_AGE_S
        for key in [k for k, e in index.items() if e.get("t", 0) < cutoff]:
            del index[key]
        keep = {vid for e in index.values() for vid in e.get("v", [])}
        for f in files:
            if f.name.split(".")[0].split("-")[0] not in keep:
                f.unlink(missing_ok=True)
        _write_json(self.dir / "index.json", index)

    # ------------------------------------------------------------------ result

    def result(self, variants: list[dict[str, Any] | None], warnings: list[str], took: float, typeset: int) -> dict[str, Any]:
        meta = self.meta() or {}
        blocks = []
        toc = []
        for b, v in zip(self.blocks, variants):
            item: dict[str, Any] = {"start": b.start, "end": b.end, "pages": [], "error": None}
            if b.heading:
                h = heading_of(b.src)
                if h:
                    item["heading"] = h[0]
                    item["id"] = f"ch{self.chapter.id}-l{b.start}"
                    if h[1] >= 1:
                        toc.append({"id": item["id"], "title": h[2], "level": h[1], "line": b.start})
            if v:
                item["error"] = v.get("error")
                if v.get("id"):
                    item["pages"] = [
                        {"url": f"/api/courses/{self.course.id}/draft/svg/{v['id']}-{p['n']}.svg", "w": p["w"], "h": p["h"], "x": p["x"]}
                        for p in v["pages"]
                    ]
            else:
                item["error"] = "non composto"
            if item["error"] or not item["pages"]:
                item["src"] = b.src[:4000]
            blocks.append(item)
        return {
            "chapter": {"id": self.chapter.id, "title": self.chapter.title, "path": self.chapter.path, "position": self.chapter.position},
            "width": round(meta.get("width", 451.0), 2),
            "blocks": blocks,
            "toc": toc,
            "warnings": warnings,
            "typeset": typeset,
            "took_ms": round(took * 1000),
        }


class Superseded(Exception):
    pass


async def inputs_for(db: AsyncSession, course: Course) -> Inputs:
    manifest = await projects.manifest(db, course.id)
    main = await projects.get_file(db, course.id, "main.tex")
    main_tex = (main.text_content if main else None) or ""
    return Inputs(
        course.id, await projects.engine_for(db, course), preamble_of(main_tex),
        templates.with_compat(await projects.preamble_for(db, course)), manifest,
        _read_json(cache_dir(course.id) / "labels.json", {}),
    )


async def render_chapter(db: AsyncSession, course: Course, chapter: Chapter, inputs: Inputs | None = None) -> dict[str, Any]:
    """The chapter as typeset blocks: {chapter, width, blocks: [{start, end, pages: [{url, w, h, x}], error, heading?, id?, src?}],
    toc, warnings, typeset, took_ms}. Sizes are in bp; `x` is where a picture starts relative to the text's left edge."""
    t0 = time.monotonic()
    inputs = inputs or await inputs_for(db, course)
    pf = await projects.get_file(db, course.id, chapter.path)
    source = (pf.text_content if pf is not None else None) or ""
    if pf is not None and pf.text_content is None and pf.blob:
        source = blobs.read_text(pf.blob)
    async with _locks[("chapter", chapter.id)]:
        r = ChapterRender(course, chapter, source, inputs)
        plan = r.walk()
        if all(plan):
            return r.result(plan, [], time.monotonic() - t0, 0)
        typeset = 0
        tries = 0
        while True:
            try:
                variants, warnings, n = await r.typeset(db, plan)
            except Superseded:
                tries += 1
                if tries > 3:
                    raise latex.CompileServiceError("superseded") from None
                plan = r.walk()
                continue
            typeset += n
            # A label changed its number: the blocks that refer to it are typeset again (like a second LaTeX pass).
            labels = _read_json(cache_dir(course.id) / "labels.json", {})
            if labels == inputs.labels or tries >= 1:
                break
            tries = 1
            inputs.labels = labels
            r = ChapterRender(course, chapter, source, inputs)
            plan = r.walk()
            if all(plan):
                variants = plan
                break
        return r.result(variants, warnings, time.monotonic() - t0, typeset)
