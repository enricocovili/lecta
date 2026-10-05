"""The lab assistant: the course assistant's turn (agent._drive) on a lesson's laboratory. It reads the lab's files, comments
and notes, the theory lesson and its chapter; it changes the lab's text files and adds comments when asked. Changes are
applied at once; the turn's first write snapshots the lab (files by version, text in the blob store, the comment ids), so the
whole turn can be undone in one click.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..egress import gate
from ..egress.schemas import Message, Part
from ..models import ChatMessage, ChatSession, Lab, LabComment, LabFile, Lesson
from ..services import blobs, prompts
from ..services import labs as lb
from ..services import settings as settings_svc
from . import agent
from . import lab_tools as tools

MODE_NOTES = {
    "ask": "Mode: ask. Decide from the request: explain without touching anything when the user asks a question; change the lab's files with the tools when they ask for a change.",
    "edit": "Mode: edit. The user wants the change made: do it with the tools, then say briefly what you changed.",
    "explain": "Mode: explain. Answer the question; you have no writing tools and must not propose to have changed anything.",
}


def running_turn(lab_id: int) -> agent.Turn | None:
    return next((t for t in agent.RUNS.values() if t.lab_id == lab_id and not t.finished), None)


# --------------------------------------------------------------------------- snapshots, change summary, undo


async def snapshot(db: AsyncSession, lab_id: int) -> dict[str, Any]:
    files = (await db.execute(select(LabFile).where(LabFile.lab_id == lab_id))).scalars()
    comments = (await db.execute(select(LabComment.id).where(LabComment.lab_id == lab_id))).scalars().all()
    return {
        "files": {
            str(f.id): {"path": f.path, "version": f.version, "kind": f.kind, "blob": blobs.put_bytes(f.content.encode()) if f.content is not None else None}
            for f in files
        },
        "comments": sorted(comments),
    }


def _text(blob: str | None) -> list[str]:
    return blobs.read_text(blob).split("\n") if blob and blobs.exists(blob) else []


def summarize(pre: dict[str, Any], cur: dict[str, Any], comment_files: dict[str, int]) -> dict[str, Any]:
    """What the turn changed: files created or edited (with line hunks in the new text) and comments added, per file."""
    out: list[dict[str, Any]] = []
    for fid, now in sorted(cur["files"].items(), key=lambda kv: kv[1]["path"]):
        before = pre["files"].get(fid)
        added_comments = comment_files.get(fid, 0)
        if before is not None and before["version"] == now["version"] and not added_comments:
            continue
        if before is None:
            op = "create"
        elif before["version"] != now["version"]:
            op = "modify"
        else:
            op = "comment"
        added, removed, hunks = agent._diff(_text(before["blob"]) if before else [], _text(now["blob"])) if op != "comment" else (0, 0, [])
        out.append({"path": now["path"], "file_id": int(fid), "chapter_id": None, "op": op, "added": added, "removed": removed, "hunks": hunks, "comments": added_comments})
    return {"files": out}


async def _comment_files(db: AsyncSession, pre: dict[str, Any], cur: dict[str, Any]) -> dict[str, int]:
    new = set(cur["comments"]) - set(pre["comments"])
    if not new:
        return {}
    rows = (await db.execute(select(LabComment.file_id).where(LabComment.id.in_(new)))).scalars().all()
    out: dict[str, int] = {}
    for fid in rows:
        out[str(fid)] = out.get(str(fid), 0) + 1
    return out


async def undo_turn(db: AsyncSession, msg: ChatMessage) -> dict[str, Any]:
    """Put the lab back as it was before the turn. Refused (409) when a file it changed was changed again since."""
    from fastapi import HTTPException

    change = msg.change or {}
    if not change.get("pre") or change.get("status") != "applied":
        raise HTTPException(status_code=409, detail="niente da annullare")
    session = await db.get(ChatSession, msg.session_id)
    lab = (await db.execute(select(Lab).where(Lab.id == session.lab_id).with_for_update())).scalar_one_or_none()
    if lab is None:
        raise HTTPException(status_code=404, detail="Not Found")
    pre, post = change["pre"], change["post"]
    files = {str(f.id): f for f in (await db.execute(select(LabFile).where(LabFile.lab_id == lab.id))).scalars()}
    touched = sorted(fid for fid in set(pre["files"]) | set(post["files"]) if pre["files"].get(fid) != post["files"].get(fid))
    conflicts = sorted(post["files"][fid]["path"] for fid in touched if fid in post["files"] and (fid not in files or files[fid].version != post["files"][fid]["version"]))
    if conflicts:
        raise HTTPException(status_code=409, detail={"message": "Dopo questa risposta il laboratorio è cambiato: annulla prima le modifiche più recenti", "skipped": conflicts})
    restored: list[str] = []
    now = datetime.now(UTC)
    for fid in touched:
        before, f = pre["files"].get(fid), files.get(fid)
        if f is None:
            continue
        if before is None:
            await db.delete(f)  # made by the turn (its comments go with it)
        elif before.get("blob") is not None and f.content is not None:
            text = blobs.read_text(before["blob"])
            await lb.follow_comments(db, f, f.content, text)
            f.content, f.size = text, len(text.encode())
            f.version += 1
            f.updated_at = now
        restored.append(f.path)
    added = set(post["comments"]) - set(pre["comments"])
    for c in (await db.execute(select(LabComment).where(LabComment.id.in_(added), LabComment.lab_id == lab.id))).scalars() if added else []:
        await db.delete(c)
    lab.updated_at = now
    msg.change = {**change, "status": "undone", "undone_at": now.isoformat()}
    await db.commit()
    return {"ok": True, "restored": restored, "skipped": []}


# --------------------------------------------------------------------------- the turn


def _user_parts(ctx: tools.LabContext, content: str, scope: dict[str, Any], open_file: LabFile | None) -> list[Part]:
    parts: list[Part] = []
    if open_file is not None:
        parts.append(Part(type="text", text=f"The user is looking at the file {open_file.path} of the lab."))
    sel = scope.get("selection") or {}
    if sel.get("text") and open_file is not None:
        a, b = sel.get("from_line"), sel.get("to_line") or sel.get("from_line")
        parts.append(Part(type="text", text=f"The user selected lines {a}–{b} of {open_file.path}:\n" + tools.fence(ctx, str(sel["text"])[:20000], "selected lines")))
    parts.append(Part(type="text", text=content))
    return parts


async def _prepare(turn: agent.Turn, user_msg_id: int) -> agent.Bench:
    reply_id = turn.reply_id
    state: dict[str, Any] = {"pre": None}
    async with SessionLocal() as db:
        user_msg = await db.get(ChatMessage, user_msg_id)
        session = await db.get(ChatSession, user_msg.session_id)
        lab = await db.get(Lab, session.lab_id)
        lesson = await db.get(Lesson, lab.lesson_id)
        scope = user_msg.scope or {}
        mode = scope.get("mode") if scope.get("mode") in MODE_NOTES else "ask"
        open_file = await db.get(LabFile, int(scope["file_id"])) if scope.get("file_id") else None
        if open_file is not None and open_file.lab_id != lab.id:
            open_file = None
        first_text = (await db.execute(select(LabFile.path).where(LabFile.lab_id == lab.id, LabFile.kind == "text").order_by(LabFile.path).limit(1))).scalar_one_or_none()
        ai = await settings_svc.get_section(db, "ai")
        nonce = gate.new_nonce()
        ctx = tools.LabContext(lab_id=lab.id, lesson_id=lesson.id, course_id=lesson.course_id, nonce=nonce, mode=mode)
        base, _ = await prompts.render(db, "lab.system", nonce)
        overview = await tools.lab_overview(ctx, {})
        system = "\n\n".join([base, MODE_NOTES[mode], f"Current state of the lab (lab_overview at the start of this turn):\n{overview.text}\n"
                                                      f"Today is {datetime.now(UTC).strftime('%Y-%m-%d')}."])
        messages = await agent._history(db, session.id, user_msg.id)
        messages.append(Message(role="user", parts=_user_parts(ctx, user_msg.content, scope, open_file)))
        meta = {"agent": True, "lab": True, "mode": mode, "user_message": user_msg.content, "selection": scope.get("selection"),
                "file_path": open_file.path if open_file else first_text}
        lab_id, session_id, course_id = lab.id, session.id, lesson.course_id

    async def before_write(db: AsyncSession, lab: Lab) -> None:
        if state["pre"] is None:
            state["pre"] = await snapshot(db, lab.id)

    async def after_write() -> None:
        async with SessionLocal() as db:
            cur = await snapshot(db, lab_id)
            summary = summarize(state["pre"], cur, await _comment_files(db, state["pre"], cur))
        await agent._save(reply_id, change={"status": "applied", **summary, "pre": state["pre"], "post": cur})
        await turn.emit("change", {"files": summary["files"]})

    ctx.before_write, ctx.after_write = before_write, after_write
    return agent.Bench(
        system=system, messages=messages, meta=meta, mode=mode, specs=tools.specs_for(mode),
        run_tool=lambda name, args: tools.run_tool(ctx, name, args), label_for=tools.label_for,
        gctx=gate.GateContext(chat_session_id=session_id, course_id=course_id, title="Assistente del laboratorio"),
        max_steps=ai.agent_max_steps, max_tokens=ai.max_output_tokens,
    )


async def start_turn(session: ChatSession, user_msg: ChatMessage, reply: ChatMessage) -> agent.Turn:
    agent._purge()
    turn = agent.Turn(reply_id=reply.id, session_id=session.id, course_id=session.course_id, lab_id=session.lab_id)
    agent.RUNS[reply.id] = turn
    turn.task = asyncio.get_running_loop().create_task(agent._drive(turn, lambda: _prepare(turn, user_msg.id)))
    return turn
