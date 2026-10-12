"""Draft notes: comments and suggestions that readers leave on a block of someone else's published course.

A note is anchored to the block as it was shown: its LaTeX (`src`) and its lines (`start`–`end`, as `services/blocks`
splits the chapter). When the chapter changes the note is found again by that text (the matching block nearest to
where it was); when the block's own text changed it is flagged `changed` («testo cambiato»). Its author sees and
changes it while it is open; the course's owner and admins see them all and accept a suggestion (written into the
text like the draft's writing in place), reject it, or resolve a comment.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Chapter, Course, DraftNote
from ..security.access import Access, can_write, check, check_note, note_access
from ..security.auth import forbidden, require_editor, require_user
from ..services import projects
from ..services.blocks import Block, split
from .courses import load_chapter, usernames
from .preview import replace_block

router = APIRouter(dependencies=[Depends(require_user)])

BODY_LIMIT = 20_000
LATEX_LIMIT = 1024 * 1024


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


async def _blocks(db: AsyncSession, course: Course, ch: Chapter) -> list[Block]:
    pf = await projects.get_file(db, course.id, ch.path)
    return split((pf.text_content if pf is not None else None) or "")


def anchor(blocks: list[Block], src: str, start: int) -> Block | None:
    """The block whose text is `src`, the nearest to line `start` when there are several; None when none is."""
    found = [b for b in blocks if b.src == src]
    return min(found, key=lambda b: abs(b.start - start)) if found else None


def note_out(n: DraftNote, names: dict[int, str], user_id: int, blocks: list[Block] | None) -> dict[str, Any]:
    """The note, with an open one placed in the chapter as it is now (`blocks`)."""
    start, end, changed = n.start, n.end, False
    if n.status == "open" and blocks is not None:
        b = anchor(blocks, n.src, n.start)
        if b is None:
            changed = True
        else:
            start, end = b.start, b.end
    return {
        "id": n.id, "course_id": n.course_id, "chapter_id": n.chapter_id, "kind": n.kind, "status": n.status,
        "src": n.src, "start": start, "end": end, "changed": changed, "body": n.body, "proposed": n.proposed,
        "author_id": n.author_id, "author": names.get(n.author_id), "mine": n.author_id == user_id,
        "resolved_by": names.get(n.resolved_by) if n.resolved_by is not None else None,
        "created_at": n.created_at, "updated_at": n.updated_at, "resolved_at": n.resolved_at,
    }


async def _list(db: AsyncSession, acc: Access, course: Course, chapter_id: int | None = None) -> list[dict[str, Any]]:
    q = select(DraftNote).where(DraftNote.course_id == course.id)
    if chapter_id is not None:
        q = q.where(DraftNote.chapter_id == chapter_id)
    if not can_write(acc.user, course):
        q = q.where(DraftNote.author_id == acc.user.id)  # a reader sees only their own notes
    notes = (await db.execute(q.order_by(DraftNote.chapter_id, DraftNote.start, DraftNote.id))).scalars().all()
    blocks: dict[int, list[Block]] = {}
    for cid in {n.chapter_id for n in notes if n.status == "open"}:
        ch = await db.get(Chapter, cid)
        if ch is not None:
            blocks[cid] = await _blocks(db, course, ch)
    names = await usernames(db)
    return [note_out(n, names, acc.user.id, blocks.get(n.chapter_id)) for n in notes]


async def _one(db: AsyncSession, acc: Access, note: DraftNote, course: Course) -> dict[str, Any]:
    await db.refresh(note)
    ch = await db.get(Chapter, note.chapter_id)
    blocks = await _blocks(db, course, ch) if ch is not None and note.status == "open" else None
    return note_out(note, await usernames(db), acc.user.id, blocks)


@router.get("/courses/{course_id}/notes")
async def course_notes(course_id: int, acc: Access = Depends(note_access), db: AsyncSession = Depends(get_db)) -> list[dict]:
    """The notes on the course the user may see: all of them for its owner and admins, else their own."""
    return await _list(db, acc, check(acc, await db.get(Course, course_id), write=False))


@router.get("/courses/{course_id}/chapters/{chapter_id}/notes")
async def chapter_notes(course_id: int, chapter_id: int, acc: Access = Depends(note_access), db: AsyncSession = Depends(get_db)) -> list[dict]:
    c = check(acc, await db.get(Course, course_id), write=False)
    ch = await load_chapter(db, c, chapter_id)
    return await _list(db, acc, c, ch.id)


class NoteIn(BaseModel):
    kind: Literal["comment", "suggestion"]
    start: int = Field(ge=1)
    end: int = Field(ge=1)
    src: str = Field(max_length=LATEX_LIMIT)  # the block as it was shown: refused if it changed since
    body: str = Field("", max_length=BODY_LIMIT)  # the comment, or why the suggestion
    proposed: str | None = Field(None, max_length=LATEX_LIMIT)  # the block's new LaTeX (a suggestion's)


def _validate(kind: str, body: str, proposed: str | None) -> None:
    if kind == "comment" and not body.strip():
        raise _invalid("Scrivi il commento")
    if kind == "comment" and proposed is not None:
        raise _invalid("Un commento non propone testo: fai un suggerimento")
    if kind == "suggestion" and proposed is None:
        raise _invalid("Scrivi il testo che proponi")


@router.post("/courses/{course_id}/chapters/{chapter_id}/notes", status_code=201)
async def create_note(course_id: int, chapter_id: int, body: NoteIn, acc: Access = Depends(note_access), db: AsyncSession = Depends(get_db)) -> dict:
    """A comment or a suggestion on a block of a published course, by any reader but its owner."""
    c = check(acc, await db.get(Course, course_id), write=False)
    ch = await load_chapter(db, c, chapter_id)
    if c.owner_id == acc.user.id:
        raise forbidden()  # the owner changes their own text
    if not c.published:
        raise _conflict("Si commentano solo le materie pubblicate")
    _validate(body.kind, body.body, body.proposed)
    if not any(b.start == body.start and b.end == body.end and b.src == body.src for b in await _blocks(db, c, ch)):
        raise _conflict("Il testo è cambiato da quando l'hai aperto: ricarica e riprova")
    note = DraftNote(course_id=c.id, chapter_id=ch.id, author_id=acc.user.id, kind=body.kind, src=body.src,
                     start=body.start, end=body.end, body=body.body.strip(), proposed=body.proposed)
    db.add(note)
    await db.commit()
    return await _one(db, acc, note, c)


class NoteEdit(BaseModel):
    body: str | None = Field(None, max_length=BODY_LIMIT)
    proposed: str | None = Field(None, max_length=LATEX_LIMIT)


@router.patch("/notes/{note_id}")
async def edit_note(note_id: int, body: NoteEdit, acc: Access = Depends(note_access), db: AsyncSession = Depends(get_db)) -> dict:
    """The author changes the text of their note while it is open."""
    note, c = await check_note(acc, db, await db.get(DraftNote, note_id))
    if note.author_id != acc.user.id:
        raise forbidden()
    if note.status != "open":
        raise _conflict("La nota è già stata chiusa")
    text = note.body if body.body is None else body.body.strip()
    proposed = note.proposed if body.proposed is None else body.proposed
    _validate(note.kind, text, proposed)
    note.body, note.proposed, note.updated_at = text, proposed, func.now()
    await db.commit()
    return await _one(db, acc, note, c)


@router.delete("/notes/{note_id}")
async def delete_note(note_id: int, acc: Access = Depends(note_access), db: AsyncSession = Depends(get_db)) -> dict:
    """The author deletes their note while it is open; the course's owner and admins any note, any time."""
    note, c = await check_note(acc, db, await db.get(DraftNote, note_id))
    if not can_write(acc.user, c):
        if note.author_id != acc.user.id:
            raise forbidden()
        if note.status != "open":
            raise _conflict("La nota è già stata chiusa")
    await db.delete(note)
    await db.commit()
    return {"ok": True}


async def _closing(db: AsyncSession, acc: Access, note_id: int, kind: str) -> tuple[DraftNote, Course]:
    note, c = await check_note(acc, db, await db.get(DraftNote, note_id))
    if not can_write(acc.user, c):
        raise forbidden()
    if note.kind != kind:
        raise _conflict("Si accetta o si rifiuta un suggerimento, si risolve un commento")
    return note, c


async def _close(db: AsyncSession, acc: Access, note: DraftNote, status: str) -> None:
    """Close the note, if it is still open (one closing wins when two happen at once): 409 otherwise."""
    res = await db.execute(
        update(DraftNote).where(DraftNote.id == note.id, DraftNote.status == "open")
        .values(status=status, resolved_by=acc.user.id, resolved_at=func.now(), updated_at=func.now())
    )
    if res.rowcount != 1:
        raise _conflict("La nota è già stata chiusa")


@router.post("/notes/{note_id}/accept", dependencies=[Depends(require_editor)])
async def accept_note(note_id: int, acc: Access = Depends(note_access), db: AsyncSession = Depends(get_db)) -> dict:
    """The suggestion's LaTeX replaces its block, found again in the chapter: 409 if the block's text changed since."""
    note, c = await _closing(db, acc, note_id, "suggestion")
    await _close(db, acc, note, "accepted")
    ch = await db.get(Chapter, note.chapter_id)
    b = anchor(await _blocks(db, c, ch), note.src, note.start) if ch is not None else None
    if ch is None or b is None:
        raise _conflict("Il testo è cambiato da quando è stato suggerito: modificalo a mano")
    await replace_block(db, c, ch, b.start, b.end, note.src, note.proposed or "")
    await db.execute(update(DraftNote).where(DraftNote.id == note.id).values(start=b.start, end=b.end))
    await db.commit()
    return await _one(db, acc, note, c)


@router.post("/notes/{note_id}/reject", dependencies=[Depends(require_editor)])
async def reject_note(note_id: int, acc: Access = Depends(note_access), db: AsyncSession = Depends(get_db)) -> dict:
    note, c = await _closing(db, acc, note_id, "suggestion")
    await _close(db, acc, note, "rejected")
    await db.commit()
    return await _one(db, acc, note, c)


@router.post("/notes/{note_id}/resolve", dependencies=[Depends(require_editor)])
async def resolve_note(note_id: int, acc: Access = Depends(note_access), db: AsyncSession = Depends(get_db)) -> dict:
    note, c = await _closing(db, acc, note_id, "comment")
    await _close(db, acc, note, "resolved")
    await db.commit()
    return await _one(db, acc, note, c)
