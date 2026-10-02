"""LaTeX names follow the Lecta name: \\appunti… macros and comment markers in the stored projects

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-02 10:00:00

The macros (`\\appuntiimage` → `\\lectaimage`, …) and the comment markers the app looks for
(`% appunti:chapters:begin`, `% ==== Appunti: lezione N`) live inside the stored text, so the text
moves with the code:

* every text file of a course project is rewritten into a new blob (the old blob stays on disk);
* the undo snapshots of the assistant's answers point at those blobs, so their hashes follow, which
  keeps «Annulla» working (it compares the snapshot with the current files by hash);
* the chapter index follows the new hashes, so nothing is re-indexed;
* every other text / JSON column (preambles, prompts, settings, import staging, job results) gets the
  same rewrite. It only matches macro and marker forms, so a file called `appunti.jpg` or a
  chapter that talks about «appunti» is left alone.

Self-contained on purpose: a migration must keep working when the app code around it changes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op

from app.config import config

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None

TEXT_EXT = (".tex", ".bib", ".sty", ".cls", ".bst", ".txt", ".md", ".csv", ".dat", ".tikz")
HEX64 = re.compile(r"^[0-9a-f]{64}$")

_MACRO = re.compile(r"\\appunti(?=(?:imagewithtext|imagepair|image|figure|publish|lang|name|includeonly|standalone)\b)")
_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"appunti@name@"), "lecta@name@"),  # \def\appunti@name@italian@theorem, \csname appunti@name@…
    (re.compile(r"PackageWarning\{appunti\}"), "PackageWarning{lecta}"),
    (re.compile(r"appunti:chapters:"), "lecta:chapters:"),  # the managed block of main.tex
    (re.compile(r"(?m)^(% (?:====|----) )Appunti: "), r"\1Lecta: "),  # lesson blocks and appended imports
    (re.compile(r"(?m)^% Appunti default preamble"), "% Lecta default preamble"),
    (re.compile(r"added by Appunti at build time"), "added by Lecta at build time"),
    (re.compile(r"\(removed by Appunti\)"), "(removed by Lecta)"),
    (re.compile(r"APPUNTIFIGPLACEHOLDER"), "LECTAFIGPLACEHOLDER"),
]


def rewrite(s: str) -> str:
    s = _MACRO.sub(r"\\lecta", s)
    for pat, repl in _RULES:
        s = pat.sub(repl, s)
    return s


def _walk(obj: Any, mapping: dict[str, str]) -> Any:
    if isinstance(obj, str):
        return mapping.get(obj) or rewrite(obj)
    if isinstance(obj, list):
        return [_walk(v, mapping) for v in obj]
    if isinstance(obj, dict):
        return {k: _walk(v, mapping) for k, v in obj.items()}
    return obj


# ------------------------------------------------------------------------------ blobs


def _blob_path(h: str) -> Path:
    return Path(config.data_dir) / "blobs" / h[:2] / h[2:4] / h


def _rewrite_blob(h: str) -> str | None:
    """The hash of the rewritten text, or None when the blob is missing, binary or has nothing to rewrite."""
    if not HEX64.match(h):
        return None
    p = _blob_path(h)
    try:
        old = p.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    new = rewrite(old)
    if new == old:
        return None
    data = new.encode("utf-8")
    nh = hashlib.sha256(data).hexdigest()
    target = _blob_path(nh)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{nh}.{os.getpid()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
    return nh


def _is_text_path(path: str) -> bool:
    return path.lower().endswith(TEXT_EXT)


def _text_hashes(bind: sa.engine.Connection) -> set[str]:
    """Blobs of the course projects, and of the files in the assistant's undo snapshots."""
    found: set[str] = set()
    for path, blob in bind.execute(sa.text("SELECT path, blob FROM project_files")):
        if _is_text_path(path):
            found.add(blob)
    for (change,) in bind.execute(sa.text("SELECT change::text FROM chat_messages WHERE change IS NOT NULL")):
        data = json.loads(change)
        for side in ("pre", "post"):
            for path, value in ((data.get(side) or {}).get("files") or {}).items():
                if _is_text_path(path):
                    found.add(value.get("blob") if isinstance(value, dict) else value)
    return {h for h in found if isinstance(h, str)}


# ------------------------------------------------------------------------------ columns


def _sweep(bind: sa.engine.Connection, mapping: dict[str, str]) -> None:
    cols = bind.execute(sa.text("""
        SELECT c.table_name, c.column_name, c.data_type
        FROM information_schema.columns c
        JOIN information_schema.tables t USING (table_schema, table_name)
        WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
          AND c.data_type IN ('text', 'character varying', 'json', 'jsonb')
          AND c.table_name <> 'alembic_version'
        ORDER BY c.table_name, c.ordinal_position
    """)).all()
    by_table: dict[str, list[tuple[str, str]]] = {}
    for table, column, dtype in cols:
        by_table.setdefault(table, []).append((column, dtype))
    for table, columns in by_table.items():
        pk = [r[0] for r in bind.execute(sa.text("""
            SELECT a.attname FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
            WHERE i.indrelid = CAST(:t AS regclass) AND i.indisprimary ORDER BY a.attnum
        """), {"t": f'public."{table}"'})]
        where_row = " AND ".join(f'"{c}" = :k{i}' for i, c in enumerate(pk)) if pk else "ctid::text = :k0"
        key_select = ", ".join(f'"{c}"' for c in pk) if pk else "ctid::text"
        select = ", ".join(f'"{c}"::text' for c, _ in columns)
        where = " OR ".join(f"\"{c}\"::text ILIKE '%appunti%'" for c, _ in columns)
        for row in bind.execute(sa.text(f'SELECT {key_select}, {select} FROM "{table}" WHERE {where}')).all():
            n = max(len(pk), 1)
            key, values = row[:n], row[n:]
            sets: dict[str, tuple[Any, str]] = {}
            for (column, dtype), value in zip(columns, values):
                if value is None:
                    continue
                if dtype in ("json", "jsonb"):
                    old = json.loads(value)
                    new = _walk(old, mapping)
                    if new != old:
                        sets[column] = (json.dumps(new, ensure_ascii=False), dtype)
                else:
                    new_text = rewrite(value)
                    if new_text != value:
                        sets[column] = (new_text, dtype)
            if sets:
                assign = ", ".join(
                    f'"{c}" = CAST(:p{i} AS {dt})' if dt in ("json", "jsonb") else f'"{c}" = :p{i}'
                    for i, (c, (_, dt)) in enumerate(sets.items())
                )
                params = {f"p{i}": v for i, (_, (v, _)) in enumerate(sets.items())}
                params.update({f"k{i}": v for i, v in enumerate(key)})
                bind.execute(sa.text(f'UPDATE "{table}" SET {assign} WHERE {where_row}'), params)


def upgrade() -> None:
    bind = op.get_bind()
    mapping: dict[str, str] = {}
    for h in sorted(_text_hashes(bind)):
        nh = _rewrite_blob(h)
        if nh:
            mapping[h] = nh
    for old, new in mapping.items():
        bind.execute(sa.text("UPDATE project_files SET blob = :new WHERE blob = :old"), {"old": old, "new": new})
        bind.execute(sa.text("UPDATE index_state SET blob = :new WHERE blob = :old"), {"old": old, "new": new})
    # The snapshots may name a rewritten blob without containing a macro themselves.
    for mid, change in bind.execute(sa.text("SELECT id, change::text FROM chat_messages WHERE change IS NOT NULL")).all():
        old = json.loads(change)
        new = _walk(old, mapping)
        if new != old:
            bind.execute(sa.text("UPDATE chat_messages SET change = CAST(:c AS jsonb) WHERE id = :id"),
                         {"c": json.dumps(new, ensure_ascii=False), "id": mid})
    _sweep(bind, mapping)


def downgrade() -> None:
    # The old names are not restored: the code of the previous revision no longer exists in this tree.
    pass
