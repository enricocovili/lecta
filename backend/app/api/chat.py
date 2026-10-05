"""The AI assistant: chat sessions per course (or per lesson's lab), turns that run in the background (streamed as events), undo."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..egress import gate
from ..models import Chapter, ChatMessage, ChatSession, Course, Lab, LabFile, Lesson
from ..pipeline import agent, lab_agent
from ..security.auth import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])

message_out = agent.message_out


def session_out(s: ChatSession) -> dict[str, Any]:
    return {"id": s.id, "course_id": s.course_id, "chapter_id": s.chapter_id, "lab_id": s.lab_id, "title": s.title, "created_at": s.created_at, "updated_at": s.updated_at}


def _running(s: ChatSession) -> agent.Turn | None:
    return lab_agent.running_turn(s.lab_id) if s.lab_id else agent.running_turn(s.course_id)


async def _session(db: AsyncSession, sid: int) -> ChatSession:
    s = await db.get(ChatSession, sid)
    if s is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return s


@router.get("/chat/sessions")
async def list_sessions(course_id: int, chapter_id: int | None = None, lab_id: int | None = None, db: AsyncSession = Depends(get_db)) -> list[dict]:
    """The conversations about the course's text, or (with `lab_id`) those of one of its labs."""
    q = select(ChatSession).where(ChatSession.course_id == course_id).order_by(ChatSession.updated_at.desc())
    q = q.where(ChatSession.lab_id == lab_id) if lab_id is not None else q.where(ChatSession.lab_id.is_(None))
    if chapter_id is not None:
        q = q.where(ChatSession.chapter_id == chapter_id)
    return [session_out(s) for s in (await db.execute(q)).scalars()]


class SessionIn(BaseModel):
    course_id: int
    chapter_id: int | None = None
    lab_id: int | None = None  # a conversation of the lab of one of the course's lessons
    title: str | None = Field(None, max_length=200)


@router.post("/chat/sessions", status_code=201)
async def create_session(body: SessionIn, db: AsyncSession = Depends(get_db)) -> dict:
    course = await db.get(Course, body.course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Not Found")
    if body.chapter_id:
        ch = await db.get(Chapter, body.chapter_id)
        if ch is None or ch.course_id != course.id:
            raise HTTPException(status_code=404, detail="Not Found")
    if body.lab_id:
        lab = await db.get(Lab, body.lab_id)
        lesson = await db.get(Lesson, lab.lesson_id) if lab else None
        if lesson is None or lesson.course_id != course.id:
            raise HTTPException(status_code=404, detail="Not Found")
    s = ChatSession(course_id=course.id, chapter_id=None if body.lab_id else body.chapter_id, lab_id=body.lab_id, title=body.title or "Nuova conversazione")
    db.add(s)
    await db.commit()
    return session_out(s)


@router.get("/chat/sessions/{sid}")
async def get_session(sid: int, db: AsyncSession = Depends(get_db)) -> dict:
    s = await _session(db, sid)
    msgs = (await db.execute(select(ChatMessage).where(ChatMessage.session_id == sid).order_by(ChatMessage.id))).scalars().all()
    out = session_out(s)
    out["messages"] = [message_out(m) for m in msgs]
    return out


@router.delete("/chat/sessions/{sid}")
async def delete_session(sid: int, db: AsyncSession = Depends(get_db)) -> dict:
    s = await _session(db, sid)
    t = _running(s)
    if t is not None and t.session_id == s.id:
        raise HTTPException(status_code=409, detail="c'è una risposta in corso")
    await db.delete(s)
    await db.commit()
    return {"ok": True}


class SelectionIn(BaseModel):
    from_line: int | None = Field(None, ge=1)
    to_line: int | None = Field(None, ge=1)
    text: str = Field("", max_length=50000)


class ScopeIn(BaseModel):
    chapter_id: int | None = None
    file_id: int | None = None  # the lab file being looked at (a lab's conversation)
    selection: SelectionIn | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list, max_length=20)  # [{source_file_id, page}]
    mode: Literal["ask", "edit", "explain", "review"] = "ask"


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    scope: ScopeIn = ScopeIn()


@router.post("/chat/sessions/{sid}/messages")
async def post_message(sid: int, body: MessageIn, db: AsyncSession = Depends(get_db)) -> dict:
    """Store the message and start the assistant's turn in the background; follow it with GET /chat/replies/{id}/events."""
    s = await _session(db, sid)
    try:
        await gate.resolve_role(db, "chat")
    except gate.GateRefused as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if _running(s) is not None:
        raise HTTPException(status_code=409, detail="L'assistente sta ancora lavorando su questo laboratorio" if s.lab_id else "L'assistente sta ancora lavorando su questa materia")
    scope = body.scope.model_dump(exclude_none=True)
    if s.lab_id:
        if scope.get("mode") == "review":
            raise HTTPException(status_code=422, detail="La revisione è solo per il testo della materia")
        scope.pop("chapter_id", None)
        scope.pop("attachments", None)
        if scope.get("file_id"):
            f = await db.get(LabFile, int(scope["file_id"]))
            if f is None or f.lab_id != s.lab_id:
                raise HTTPException(status_code=404, detail="Not Found")
    elif scope.get("chapter_id"):
        ch = await db.get(Chapter, int(scope["chapter_id"]))
        if ch is None or ch.course_id != s.course_id:
            raise HTTPException(status_code=404, detail="Not Found")
    msg = ChatMessage(session_id=s.id, role="user", content=body.content, scope=scope, status="sent")
    db.add(msg)
    await db.flush()
    reply = ChatMessage(session_id=s.id, role="assistant", content="", status="streaming", reply_to=msg.id)
    db.add(reply)
    s.updated_at = datetime.now(UTC)
    if s.title in ("", "Nuova conversazione", "Review chat"):
        s.title = body.content[:80]
    await db.commit()
    await (lab_agent.start_turn if s.lab_id else agent.start_turn)(s, msg, reply)
    return {"message": message_out(msg), "reply": message_out(reply)}


def _sse(seq: int | None, event: str, data: Any) -> str:
    head = f"id: {seq}\n" if seq else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _reply(db: AsyncSession, rid: int) -> ChatMessage:
    m = await db.get(ChatMessage, rid)
    if m is None or m.role != "assistant":
        raise HTTPException(status_code=404, detail="Not Found")
    return m


@router.get("/chat/replies/{rid}/events")
async def reply_events(rid: int, after: int = Query(0, ge=0), db: AsyncSession = Depends(get_db)) -> StreamingResponse:
    """The events of a reply (text, tool, tool_done, change, done, error): replayed from the start or after `after`, then live."""
    m = await _reply(db, rid)
    turn = agent.RUNS.get(rid)
    if turn is None and m.status == "streaming":
        # The server restarted while this turn was running.
        m.status, m.error = "error", "interrotto: il server è stato riavviato"
        await db.commit()
    final = message_out(m)

    async def gen():
        if turn is None:
            if final["status"] == "error":
                yield _sse(None, "error", {"message": final["error"], "reply": final})
            else:
                yield _sse(None, "done", {"reply": final})
            return
        async for seq, event, data in agent.stream_events(turn, after):
            yield _sse(seq, event, data)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.post("/chat/replies/{rid}/cancel")
async def cancel_reply(rid: int, db: AsyncSession = Depends(get_db)) -> dict:
    await _reply(db, rid)
    turn = agent.RUNS.get(rid)
    if turn is None or turn.finished:
        return {"ok": True, "running": False}
    turn.cancel = True
    return {"ok": True, "running": True}


@router.post("/chat/replies/{rid}/undo")
async def undo_reply(rid: int, db: AsyncSession = Depends(get_db)) -> dict:
    m = await _reply(db, rid)
    if m.status == "streaming":
        raise HTTPException(status_code=409, detail="la risposta è ancora in corso")
    s = await _session(db, m.session_id)
    return await (lab_agent.undo_turn if s.lab_id else agent.undo_turn)(db, m)


@router.get("/courses/{course_id}/review/latest")
async def latest_review(course_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    course = await db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Not Found")
    row = (
        await db.execute(
            select(ChatMessage)
            .join(ChatSession, ChatSession.id == ChatMessage.session_id)
            .where(ChatSession.course_id == course_id, ChatSession.lab_id.is_(None), ChatMessage.review.is_not(None), ChatMessage.status == "done")
            .order_by(ChatMessage.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return {"review": None, "message_id": None, "at": None}
    return {"review": row.review, "message_id": row.id, "at": row.created_at, "stale": course.updated_at > row.created_at}
