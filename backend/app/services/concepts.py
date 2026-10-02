"""Concept map: sections that appear in more than one course.

Units are the chapters, sections and subsections of every course, parsed from
the current chapter sources. Two units of DIFFERENT courses are linked when

- their titles match lexically (normalised, accent- and stopword-insensitive:
  equal, high token overlap, or one containing the other), always; and
- their stored embeddings are close (average of the section's index chunks,
  compared in SQL with pgvector's cosine operator), when semantic search is on.

Linked units are clustered (union-find) into concepts spanning ≥ 2 courses.
Nothing is stored: the result is recomputed whenever sources, chapters or the
index change (a cheap fingerprint query guards an in-process cache).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Chapter, Course, ProjectFile
from . import overview, semantic
from .texttools import _STOP, latex_to_text

# Semantic links: raw cosine, normalised with semantic.normalise (0..1), must reach this…
SEMANTIC_MIN = 0.7
# …and, once there are enough units for a meaningful mean, so must the cosine of the vectors
# centred on the mean of all units: this discounts what every section shares (language,
# the author's style, boilerplate) and keeps only what is specific to the topic.
CENTERED_MIN = 0.45
CENTER_MIN_UNITS = 12
# Lexical links: token-set overlap (Jaccard) for titles of ≥ 2 content words.
JACCARD_MIN = 0.6
MAX_CONCEPTS = 24

_EXTRA_STOP = (
    "il lo la i gli le un una uno di a da in con su per tra fra e o ed del dello della dei degli delle al allo alla ai "
    "agli alle dal dallo dalla dai dagli dalle nel nello nella nei negli nelle sul sullo sulla sui sugli sulle "
    "the a an of and or for to in on with at by from vs versus"
).split()
STOPWORDS = frozenset({w for ws in _STOP.values() for w in ws} | set(_EXTRA_STOP))

# Headings that say nothing about the topic: never concepts on their own.
GENERIC = frozenset(
    (
        "introduzione intro introduction premessa prefazione motivazione motivazioni obiettivi panoramica overview "
        "notazione notazioni notation storia history cenni richiami preliminari preliminaries definizioni definitions "
        "esempi esempio examples example esercizi esercizio exercises esercitazione riepilogo sommario summary "
        "conclusioni conclusione conclusions conclusion appendice appendix bibliografia riferimenti references "
        "domande note varie altro approfondimenti"
    ).split()
)

_HEAD_RE = re.compile(r"\\(chapter|section|subsection)(\*?)\s*(?:\[[^\]]*\])?\s*\{((?:[^{}]|\{[^{}]*\})*)\}")
# chunk_chapter()'s own heading syntax: IndexChunk.heading only exists for these.
_CHUNK_HEAD_RE = re.compile(r"\s*\\(chapter|section|subsection|subsubsection)\*?\s*(\[[^\]]*\])?\s*\{([^{}]*)\}")
_MATH_RE = re.compile(r"\$[^$]*\$|\\\(.*?\\\)")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _deaccent(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def clean_title(title: str) -> str:
    """Readable title: LaTeX markup and inline math removed."""
    s = latex_to_text(_MATH_RE.sub(" ", title)).replace("\n", " ")
    return re.sub(r"\s+", " ", s).strip(" .:;,-")


def _stem(tok: str) -> str:
    # Crude Italian/English plural folding: "reti"/"rete", "metodi"/"metodo", "networks"/"network".
    return tok[:-1] if len(tok) > 4 and tok[-1] in "aeios" else tok


def title_tokens(title: str) -> frozenset[str]:
    """Content words of a title (lowercase, no accents, no stopwords, plural-folded)."""
    words = _TOKEN_RE.findall(_deaccent(clean_title(title)).lower())
    return frozenset(_stem(w) for w in words if len(w) > 1 and w not in STOPWORDS)


_GENERIC_STEMS = frozenset(_stem(w) for w in GENERIC)


def _is_generic(tokens: frozenset[str]) -> bool:
    return not tokens or tokens <= _GENERIC_STEMS


def lexical_score(a: frozenset[str], b: frozenset[str]) -> float | None:
    """Similarity of two titles' token sets, or None when they don't match."""
    if not a or not b:
        return None
    if a == b:
        return 1.0
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    inter = len(a & b)
    jac = inter / len(a | b)
    if len(small) >= 2 and small <= large:
        return round(max(jac, 0.7), 4)
    if len(small) >= 2 and jac >= JACCARD_MIN:
        return round(jac, 4)
    return None


@dataclass
class Unit:
    course_id: int
    chapter_id: int
    chapter_title: str
    chapter_number: int
    level: str  # chapter | section | subsection
    title: str  # readable, no numbering
    number: str | None  # "4", "4.1", "4.1.2"; None for starred headings
    path: str
    line: int
    tokens: frozenset[str]
    chunk_heading: str | None  # IndexChunk.heading of this unit's chunks, when indexable

    @property
    def key(self) -> str:
        return f"{self.chapter_id}:{self.level}:{' '.join(sorted(self.tokens))}"


def parse_units(course_id: int, chapter_id: int, position: int, chapter_title: str, path: str, source: str) -> list[Unit]:
    """The chapter itself plus its sections/subsections, with numbering and 1-based line numbers."""
    units = [
        Unit(course_id, chapter_id, chapter_title, position, "chapter", clean_title(chapter_title), str(position), path, 1,
             title_tokens(chapter_title), None)
    ]
    sec = sub = 0
    for i, raw in enumerate(source.split("\n"), start=1):
        line = re.sub(r"(?<!\\)%.*", "", raw)
        m = _HEAD_RE.search(line)
        if not m:
            continue
        level, star, title = m.group(1), m.group(2), m.group(3).strip()
        if level == "chapter":
            units[0].line = i
            continue
        number: str | None = None
        if level == "section":
            if not star:
                sec, sub = sec + 1, 0
                number = f"{position}.{sec}"
        elif not star:
            sub += 1
            number = f"{position}.{sec}.{sub}"
        cm = _CHUNK_HEAD_RE.match(line)
        head = f"{chapter_title} / {cm.group(3).strip()}" if cm and cm.group(1) == level else None
        units.append(Unit(course_id, chapter_id, chapter_title, position, level, clean_title(title), number, path, i, title_tokens(title), head))
    return [u for u in units if u.title and not _is_generic(u.tokens)]


class _DSU:
    def __init__(self, n: int) -> None:
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def lexical_pairs(units: list[Unit]) -> dict[tuple[int, int], float]:
    """Cross-course lexical matches {(i, j): score} with i < j (candidates via an inverted token index)."""
    by_token: dict[str, list[int]] = {}
    for i, u in enumerate(units):
        for t in u.tokens:
            by_token.setdefault(t, []).append(i)
    out: dict[tuple[int, int], float] = {}
    for i, u in enumerate(units):
        cands = {j for t in u.tokens for j in by_token[t] if j > i and units[j].course_id != u.course_id}
        for j in cands:
            s = lexical_score(u.tokens, units[j].tokens)
            if s is not None:
                out[(i, j)] = s
    return out


async def semantic_pairs(db: AsyncSession, units: list[Unit], model: str) -> dict[tuple[int, int], float]:
    """Cross-course embedding matches {(i, j): normalised similarity}, mutual best per course pair."""
    index = {(u.chapter_id, u.chunk_heading): i for i, u in reversed(list(enumerate(units))) if u.chunk_heading}
    if not index:
        return {}
    rows = (
        await db.execute(
            text(
                """
                WITH u AS (
                    SELECT course_id, chapter_id, heading, avg(embedding) AS v
                    FROM index_chunks
                    WHERE embedding IS NOT NULL AND model = :m AND position(' / ' IN heading) > 0
                    GROUP BY course_id, chapter_id, heading
                ), g AS (SELECT avg(v) AS m, count(*) AS n FROM u)
                SELECT a.chapter_id, a.heading, b.chapter_id, b.heading,
                       1 - (a.v <=> b.v) AS sim,
                       CASE WHEN g.n >= :min_units THEN 1 - ((a.v - g.m) <=> (b.v - g.m)) END AS csim
                FROM u a JOIN u b ON a.course_id < b.course_id CROSS JOIN g
                WHERE 1 - (a.v <=> b.v) >= :cut
                """
            ),
            {"m": model, "cut": 0.2 + 0.6 * SEMANTIC_MIN, "min_units": CENTER_MIN_UNITS},
        )
    ).all()
    # Keep only mutual best matches between each pair of courses, so a broad section can't chain everything together.
    best: dict[tuple[int, int], tuple[float, int]] = {}  # (unit, other course) -> (sim, other unit)
    cand: list[tuple[int, int, float]] = []
    for ach, ahead, bch, bhead, sim, csim in rows:
        i, j = index.get((ach, ahead)), index.get((bch, bhead))
        if i is None or j is None or (csim is not None and float(csim) < CENTERED_MIN):
            continue
        sim = float(sim)
        cand.append((i, j, sim))
        for x, y in ((i, j), (j, i)):
            k = (x, units[y].course_id)
            if k not in best or sim > best[k][0]:
                best[k] = (sim, y)
    out: dict[tuple[int, int], float] = {}
    for i, j, sim in cand:
        if best[(i, units[j].course_id)][1] == j and best[(j, units[i].course_id)][1] == i:
            out[(min(i, j), max(i, j))] = round(semantic.normalise(sim), 4)
    return out


@dataclass
class Concept:
    id: str
    label: str
    score: float
    units: list[Unit] = field(default_factory=list)

    @property
    def course_ids(self) -> list[int]:
        return sorted({u.course_id for u in self.units})


def cluster(units: list[Unit], pairs: dict[tuple[int, int], float]) -> list[Concept]:
    dsu = _DSU(len(units))
    for i, j in pairs:
        dsu.union(i, j)
    groups: dict[int, list[int]] = {}
    for i, j in pairs:
        groups.setdefault(dsu.find(i), [])
    for i in range(len(units)):
        r = dsu.find(i)
        if r in groups:
            groups[r].append(i)
    scores: dict[int, float] = {}
    for (i, _j), s in pairs.items():
        r = dsu.find(i)
        scores[r] = max(scores.get(r, 0.0), s)
    out = []
    for r, members in groups.items():
        us = sorted((units[i] for i in members), key=lambda u: (u.course_id, u.chapter_number, u.line))
        if len({u.course_id for u in us}) < 2:
            continue
        # Label: the shortest clean title (fewest words, then fewest characters, no math preferred).
        label = min(us, key=lambda u: (len(u.tokens), len(u.title), u.title)).title
        cid = hashlib.sha1("|".join(sorted(u.key for u in us)).encode()).hexdigest()[:12]
        out.append(Concept(cid, label, scores[r], us))
    # Most courses first, then the best matches; ties go to concepts between well-connected courses.
    degree: dict[int, int] = {}
    for c in out:
        for cid in c.course_ids:
            degree[cid] = degree.get(cid, 0) + 1
    out.sort(key=lambda c: (-len(c.course_ids), -c.score, -sum(degree[i] for i in c.course_ids), c.label.lower()))
    return out[:MAX_CONCEPTS]


async def _fingerprint(db: AsyncSession) -> tuple[Any, ...]:
    row = (
        await db.execute(
            text(
                """
                SELECT (SELECT max(updated_at) FROM project_files WHERE path LIKE '%.tex'),
                       (SELECT count(*) FROM project_files WHERE path LIKE '%.tex'),
                       (SELECT max(updated_at) FROM chapters), (SELECT count(*) FROM chapters),
                       (SELECT max(updated_at) FROM index_chunks), (SELECT count(embedding) FROM index_chunks)
                """
            )
        )
    ).one()
    return tuple(row)


_cache: dict[str, Any] = {}


async def _compute(db: AsyncSession, st: dict[str, Any]) -> tuple[str, list[Concept]]:
    rows = (
        await db.execute(
            select(Chapter.id, Chapter.course_id, Chapter.position, Chapter.title, Chapter.path, ProjectFile.text_content)
            .join(ProjectFile, (ProjectFile.course_id == Chapter.course_id) & (ProjectFile.path == Chapter.path))
            .order_by(Chapter.course_id, Chapter.position)
        )
    ).all()
    units: list[Unit] = []
    for ch_id, course_id, position, title, path, src in rows:
        units.extend(parse_units(course_id, ch_id, position, title, path, src or ""))
    pairs = lexical_pairs(units)
    mode = "lexical"
    have = st["enabled"] and (
        await db.execute(text("SELECT EXISTS (SELECT 1 FROM index_chunks WHERE embedding IS NOT NULL AND model = :m)"), {"m": st["model"]})
    ).scalar()
    if have:
        mode = "semantic"
        for k, s in (await semantic_pairs(db, units, st["model"])).items():
            pairs[k] = max(pairs.get(k, 0.0), s)
    return mode, cluster(units, pairs)


async def concept_map(db: AsyncSession) -> dict[str, Any]:
    st = await semantic.status(db)
    fp = (await _fingerprint(db), st.get("model"), st["enabled"])
    if _cache.get("fp") != fp:
        _cache["fp"], _cache["value"] = fp, await _compute(db, st)
    mode, concepts = _cache["value"]

    courses = (await db.execute(select(Course.id, Course.name, Course.slug, Course.published).order_by(Course.name))).all()
    names = {cid: name for cid, name, _, _ in courses}
    states = await overview.course_states(db)
    counts: dict[int, int] = {}
    for c in concepts:
        for cid in c.course_ids:
            counts[cid] = counts.get(cid, 0) + 1
    return {
        "mode": mode,
        "courses": [
            {
                "id": cid,
                "name": name,
                "slug": slug,
                "status": states.get(cid, {}).get("status", "ok"),
                "published": published,
                "concept_count": counts.get(cid, 0),
            }
            for cid, name, slug, published in courses
        ],
        "concepts": [
            {
                "id": c.id,
                "label": c.label,
                "score": c.score,
                "course_ids": c.course_ids,
                "occurrences": [
                    {
                        "course_id": u.course_id,
                        "course_name": names.get(u.course_id, ""),
                        "chapter_id": u.chapter_id,
                        "chapter_title": clean_title(u.chapter_title),
                        "chapter_number": u.chapter_number,
                        "level": u.level,
                        "section": u.title,
                        "section_number": u.number,
                        "path": u.path,
                        "line": u.line,
                    }
                    for u in c.units
                    if u.course_id in names
                ],
            }
            for c in concepts
        ],
    }
