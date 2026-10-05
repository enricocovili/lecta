"""The assistant: a tool-using agent that works on one course.

A turn runs in the background (it survives the browser closing): the model reads and changes the
course through the tools in agent_tools.py, its text and tool activity stream to the UI as events, and
the changes are applied at once. The first write of a turn snapshots the course, so the whole turn can
be undone in one click.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..egress import gate
from ..egress.schemas import EgressRequest, EgressResult, Message, Part, ToolCall
from ..models import Chapter, ChatMessage, ChatSession, Course, ProjectFile
from ..services import blobs, projects, prompts
from ..services import settings as settings_svc
from . import agent_tools as tools
from . import apply as prop
from .requests import image_mime

log = logging.getLogger("lecta.agent")

HISTORY = 10
KEEP_FINISHED_S = 600
MAX_HISTORY_CHARS = 12_000
PRUNE_AFTER_CHARS = 400_000
RETRY_KINDS = ("rate_limit", "timeout", "network", "provider_error")
_SUGGEST_RE = re.compile(r"```suggestions\s*(.*?)```", re.S)
_REVIEW_RE = re.compile(r"```review\s*(.*?)```", re.S)


# --------------------------------------------------------------------------- live turns


@dataclass
class Turn:
    """A running (or just finished) turn: its events are kept so a reconnecting browser can replay them."""

    reply_id: int
    session_id: int
    course_id: int
    lab_id: int | None = None  # a turn of a lab's assistant (lab_agent.py)
    events: list[tuple[int, str, dict[str, Any]]] = field(default_factory=list)
    finished: bool = False
    finished_at: float = 0.0
    cancel: bool = False
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task | None = None

    async def emit(self, event: str, data: dict[str, Any]) -> None:
        async with self.cond:
            self.events.append((len(self.events) + 1, event, data))
            self.cond.notify_all()

    async def finish(self) -> None:
        async with self.cond:
            self.finished, self.finished_at = True, time.monotonic()
            self.cond.notify_all()


RUNS: dict[int, Turn] = {}


def _purge() -> None:
    now = time.monotonic()
    for rid in [r for r, t in RUNS.items() if t.finished and now - t.finished_at > KEEP_FINISHED_S]:
        RUNS.pop(rid, None)


def running_turn(course_id: int) -> Turn | None:
    """The course assistant's turn running on this course (a lab's assistant runs apart: lab_agent.running_turn)."""
    return next((t for t in RUNS.values() if t.course_id == course_id and t.lab_id is None and not t.finished), None)


async def stream_events(turn: Turn, after: int = 0):
    """Yield (seq, event, data) from `after`, then live, until the turn ends."""
    i = after
    while True:
        async with turn.cond:
            await turn.cond.wait_for(lambda: i < len(turn.events) or turn.finished)
            batch = turn.events[i:]
            finished = turn.finished
        for item in batch:
            yield item
        i += len(batch)
        if finished and not batch:
            return


# --------------------------------------------------------------------------- output shape


def strip_blocks(text: str) -> str:
    return _REVIEW_RE.sub("", _SUGGEST_RE.sub("", text)).strip()


def parse_suggestions(text: str) -> list[str]:
    m = _SUGGEST_RE.search(text)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return []
    return [str(s).strip()[:160] for s in data if str(s).strip()][:4] if isinstance(data, list) else []


def parse_review(text: str) -> dict[str, Any] | None:
    m = _REVIEW_RE.search(text)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    score = data.get("score")
    issues = []
    for it in data.get("issues") or []:
        if isinstance(it, dict) and str(it.get("text") or "").strip():
            issues.append({"chapter": str(it.get("chapter") or "")[:200], "chapter_id": it.get("chapter_id") if isinstance(it.get("chapter_id"), int) else None,
                           "text": str(it["text"])[:600], "fix": str(it.get("fix") or it["text"])[:600]})
    return {
        "verdict": str(data.get("verdict") or "")[:600],
        "score": int(score) if isinstance(score, int | float) and 0 <= score <= 10 else None,
        "strengths": [str(s)[:300] for s in (data.get("strengths") or []) if str(s).strip()][:8],
        "issues": issues[:20],
    }


def message_out(m: ChatMessage) -> dict[str, Any]:
    change = None
    if m.change:
        change = {k: v for k, v in m.change.items() if k not in ("pre", "post")}
    return {
        "id": m.id, "role": m.role, "content": m.content if m.role == "user" else strip_blocks(m.content), "status": m.status,
        "scope": m.scope, "steps": m.steps or [], "change": change, "suggestions": m.suggestions or [], "review": m.review,
        "reply_to": m.reply_to, "error": m.error, "tokens_in": m.tokens_in, "tokens_out": m.tokens_out, "cost_usd": m.cost_usd,
        "created_at": m.created_at,
    }


# --------------------------------------------------------------------------- snapshots, change summary, undo


async def snapshot_state(db: AsyncSession, course: Course) -> dict[str, Any]:
    rows = (await db.execute(select(ProjectFile.path, ProjectFile.blob, ProjectFile.meta).where(ProjectFile.course_id == course.id))).all()
    chapters = await projects.chapters_of(db, course.id)
    return {
        "files": {p: {"blob": b, "meta": m or {}} for p, b, m in rows},
        "chapters": [{"id": c.id, "slug": c.slug, "title": c.title, "position": c.position, "path": c.path} for c in chapters],
        "preamble": course.preamble_override,
    }


def _lines(blob: str | None) -> list[str]:
    if not blob or not blobs.exists(blob):
        return []
    return blobs.read_text(blob).split("\n")


def _diff(old: list[str], new: list[str]) -> tuple[int, int, list[dict[str, int]]]:
    sm = difflib.SequenceMatcher(None, old, new, autojunk=len(old) + len(new) > 6000)
    added = removed = 0
    hunks: list[dict[str, int]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        added += j2 - j1
        removed += i2 - i1
        hunks.append({"from_line": j1 + 1, "to_line": max(j1 + 1, j2)})
    return added, removed, hunks[:50]


def summarize(pre: dict[str, Any], cur: dict[str, Any]) -> dict[str, Any]:
    """What changed between two snapshots: files (with line hunks in the new text) and chapters."""
    pre_files = {p: v["blob"] for p, v in pre["files"].items()}
    cur_files = {p: v["blob"] for p, v in cur["files"].items()}
    pre_ch = {c["id"]: c for c in pre["chapters"]}
    cur_ch = {c["id"]: c for c in cur["chapters"]}
    path_to_ch = {c["path"]: c["id"] for c in cur["chapters"]}
    files: list[dict[str, Any]] = []
    handled: set[str] = set()
    # Chapters keep their identity when a renumbering renames their file.
    for cid, c in cur_ch.items():
        old = pre_ch.get(cid)
        if old is None:
            continue
        handled |= {old["path"], c["path"]}
        a, b = pre_files.get(old["path"]), cur_files.get(c["path"])
        if a == b and old["path"] == c["path"]:
            continue
        added, removed, hunks = _diff(_lines(a), _lines(b)) if a != b else (0, 0, [])
        if a != b or old["path"] != c["path"]:
            files.append({"path": c["path"], "chapter_id": cid, "op": "modify" if a != b else "rename", "added": added, "removed": removed,
                          "hunks": hunks, **({"old_path": old["path"]} if old["path"] != c["path"] else {})})
    for p in sorted((set(pre_files) | set(cur_files)) - handled):
        a, b = pre_files.get(p), cur_files.get(p)
        if a == b:
            continue
        is_text = p.rsplit(".", 1)[-1].lower() in {e.lstrip(".") for e in projects.TEXT_EXT}
        if a is None:
            op = "create"
        elif b is None:
            op = "delete"
        else:
            op = "modify"
        added, removed, hunks = _diff(_lines(a) if is_text else [], _lines(b) if is_text else []) if is_text else (0, 0, [])
        files.append({"path": p, "chapter_id": path_to_ch.get(p) or next((c["id"] for c in pre["chapters"] if c["path"] == p), None),
                      "op": op, "added": added, "removed": removed, "hunks": hunks})
    if pre.get("preamble") != cur.get("preamble") and not any(f["path"] == "preamble.tex" for f in files):
        files.append({"path": "preamble.tex", "chapter_id": None, "op": "modify", "added": 0, "removed": 0, "hunks": []})
    chapters: list[dict[str, Any]] = []
    for cid, c in cur_ch.items():
        if cid not in pre_ch:
            chapters.append({"id": cid, "title": c["title"], "op": "created"})
        elif pre_ch[cid]["title"] != c["title"]:
            chapters.append({"id": cid, "title": c["title"], "op": "renamed", "old_title": pre_ch[cid]["title"]})
    for cid, c in pre_ch.items():
        if cid not in cur_ch:
            chapters.append({"id": cid, "title": c["title"], "op": "deleted"})
    common = [c["id"] for c in sorted(cur["chapters"], key=lambda c: c["position"]) if c["id"] in pre_ch]
    common_pre = [c["id"] for c in sorted(pre["chapters"], key=lambda c: c["position"]) if c["id"] in cur_ch]
    if common != common_pre:
        chapters += [{"id": cid, "title": cur_ch[cid]["title"], "op": "moved"} for cid in common if common.index(cid) != common_pre.index(cid)]
    return {"files": files, "chapters": chapters}


async def undo_turn(db: AsyncSession, msg: ChatMessage) -> dict[str, Any]:
    """Restore what the turn changed. Refused (409) when those files changed again since."""
    from fastapi import HTTPException

    change = msg.change or {}
    if not change.get("pre") or change.get("status") != "applied":
        raise HTTPException(status_code=409, detail="niente da annullare")
    session = await db.get(ChatSession, msg.session_id)
    course = (await db.execute(select(Course).where(Course.id == session.course_id).with_for_update())).scalar_one()
    pre, post = change["pre"], change["post"]
    cur = await snapshot_state(db, course)
    pre_blobs = {p: v["blob"] for p, v in pre["files"].items()}
    touched = {p for p in set(pre_blobs) | set(post["files"]) if pre_blobs.get(p) != post["files"].get(p)}
    conflicts = sorted(p for p in touched if cur["files"].get(p, {}).get("blob") != post["files"].get(p))
    if pre.get("preamble") != post.get("preamble") and cur.get("preamble") != post.get("preamble"):
        conflicts.append("preamble.tex")
    if conflicts:
        raise HTTPException(status_code=409, detail={
            "message": "Dopo questa risposta il documento è cambiato: annulla prima le modifiche più recenti", "skipped": conflicts})
    restored: list[str] = []
    for p in sorted(touched - set(pre_blobs)):
        await projects.delete_file(db, course, p)
        restored.append(p)
    for p in sorted(touched & set(pre_blobs)):
        await projects.write_file(db, course, p, blobs.read_bytes(pre_blobs[p]), meta=pre["files"][p].get("meta") or {}, validated=True)
        restored.append(p)
    # Chapters: same rows, same order, same names.
    pre_ids = {c["id"] for c in pre["chapters"]}
    cur_rows = {c.id: c for c in await projects.chapters_of(db, course.id)}
    for cid, row in cur_rows.items():
        if cid not in pre_ids:
            await db.delete(row)
    await db.flush()
    for cid, row in cur_rows.items():
        if cid in pre_ids:
            row.slug = f"_undo{cid}"
    await db.flush()
    for c in pre["chapters"]:
        row = cur_rows.get(c["id"])
        if row is None or c["id"] not in cur_rows:
            row = Chapter(id=c["id"], course_id=course.id, position=c["position"], slug=c["slug"], title=c["title"], path=c["path"])
            db.add(row)
        row.slug, row.title, row.position, row.path = c["slug"], c["title"], c["position"], c["path"]
        row.updated_at = datetime.now(UTC)
    course.preamble_override = pre.get("preamble")
    course.updated_at = datetime.now(UTC)
    msg.change = {**change, "status": "undone", "undone_at": datetime.now(UTC).isoformat()}
    await db.commit()
    prop.recompile_later(course.id, None)
    return {"ok": True, "restored": restored, "skipped": []}


# --------------------------------------------------------------------------- prompts and context


def _scope_note(scope: dict[str, Any], chapter: Chapter | None) -> str:
    bits: list[str] = []
    if chapter is not None:
        bits.append(f"The user is looking at chapter «{chapter.title}» (id {chapter.id}, {chapter.path}).")
    return " ".join(bits)


async def _history(db: AsyncSession, session_id: int, before_id: int) -> list[Message]:
    rows = (
        await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id, ChatMessage.id < before_id, ChatMessage.status.in_(("sent", "done", "cancelled")))
            .order_by(ChatMessage.id.desc())
            .limit(HISTORY)
        )
    ).scalars().all()
    out: list[Message] = []
    for h in reversed(rows):
        if h.role == "user":
            out.append(Message(role="user", parts=[Part(type="text", text=h.content[:MAX_HISTORY_CHARS])]))
            continue
        text = strip_blocks(h.content)[:MAX_HISTORY_CHARS]
        ch = h.change or {}
        if ch.get("files"):
            what = ", ".join(f"{f['path']} ({f['op']})" for f in ch["files"][:8])
            text += f"\n[Modifiche fatte in quel turno ({'annullate dall’utente' if ch.get('status') == 'undone' else 'applicate'}): {what}]"
        if text.strip():
            out.append(Message(role="assistant", parts=[Part(type="text", text=text)]))
    merged: list[Message] = []
    for m in out:
        if merged and merged[-1].role == m.role:
            merged[-1] = Message(role=m.role, parts=merged[-1].parts + m.parts)
        else:
            merged.append(m)
    while merged and merged[0].role != "user":
        merged.pop(0)
    return merged


MODE_NOTES = {
    "ask": "Mode: ask. Decide from the request: explain without touching anything when the user asks a question; change the course with the tools when they ask for a change.",
    "edit": "Mode: edit. The user wants the change made: do it with the tools, then say briefly what you changed.",
    "explain": "Mode: explain. Answer the question or doubt; you have no writing tools and must not propose to have changed anything.",
    "review": "Mode: review. Give feedback on the whole document as described in the review instructions; you have no writing tools.",
}


async def _system(db: AsyncSession, ctx: tools.ToolContext, course: Course, mode: str) -> str:
    text, _ = await prompts.render(db, "agent.system", ctx.nonce)
    parts = [text, MODE_NOTES.get(mode, MODE_NOTES["ask"])]
    if mode == "review":
        parts.append((await prompts.render(db, "agent.review", ctx.nonce))[0])
    overview = await tools.course_overview(ctx, {})
    parts.append(f"Current state of the course (course_overview at the start of this turn):\n{overview.text}\n"
                 f"Language of the notes: {course.language}. Today is {datetime.now(UTC).strftime('%Y-%m-%d')}.")
    if (course.guidelines or "").strip():
        parts.append("The student's guidelines for writing this subject (follow them whenever you write or rewrite text of the course, "
                     f"unless the request says otherwise):\n{course.guidelines.strip()}")
    return "\n\n".join(parts)


def _user_parts(ctx: tools.ToolContext, content: str, scope: dict[str, Any], chapter: Chapter | None, attachments: list[Part]) -> list[Part]:
    parts: list[Part] = []
    note = _scope_note(scope, chapter)
    sel = scope.get("selection") or {}
    if note:
        parts.append(Part(type="text", text=note))
    if sel.get("text"):
        where = f"lines {sel.get('from_line')}–{sel.get('to_line') or sel.get('from_line')} of {chapter.path}" if chapter and sel.get("from_line") else "the open chapter"
        parts.append(Part(type="text", text=f"The user selected this text in the draft ({where}; maths shown as $tex$). "
                                            "Locate it in the source with grep/read_file before editing it:\n" + tools.fence(ctx, str(sel["text"])[:20000], "selected text")))
    parts += attachments
    parts.append(Part(type="text", text=content))
    return parts


async def _attachments(db: AsyncSession, ctx: tools.ToolContext, scope: dict[str, Any]) -> list[Part]:
    out: list[Part] = []
    for att in (scope.get("attachments") or [])[:10]:
        try:
            sid = int(att["source_file_id"])
        except (KeyError, TypeError, ValueError):
            continue
        args: dict[str, Any] = {"source_file_id": sid}
        if att.get("page"):
            args["page"] = int(att["page"])
        res = await tools.run_tool(ctx, "view_source_page" if att.get("page") else "read_source", args)
        out.append(Part(type="text", text=f"Attachment: {res.text}"))
        for im in res.images:
            out.append(Part(type="image", blob=im["blob"], mime=im["mime"], width=im["width"], height=im["height"], label=im["label"]))
    return out


def _prune(messages: list[Message]) -> None:
    """Keep the transcript bounded: old tool results shrink to their first lines."""
    total = sum(len(p.text or "") for m in messages for p in m.parts if p.type == "tool_result")
    if total <= PRUNE_AFTER_CHARS:
        return
    for m in messages[:-4]:
        for p in m.parts:
            if p.type == "tool_result" and len(p.text or "") > 800:
                p.text = (p.text or "")[:600] + "\n… [risultato accorciato: rileggi il file se ti serve]"


# --------------------------------------------------------------------------- the loop


async def start_turn(session: ChatSession, user_msg: ChatMessage, reply: ChatMessage) -> Turn:
    _purge()
    turn = Turn(reply_id=reply.id, session_id=session.id, course_id=session.course_id)
    RUNS[reply.id] = turn
    turn.task = asyncio.get_running_loop().create_task(_run(turn, user_msg.id))
    return turn


async def _save(reply_id: int, **fields: Any) -> None:
    async with SessionLocal() as db:
        r = await db.get(ChatMessage, reply_id)
        if r is None:
            return
        for k, v in fields.items():
            setattr(r, k, v)
        await db.commit()


@dataclass
class Bench:
    """What a turn works with: the prompt and the conversation so far, the tools and how to run them, and what to do at the end.
    The course assistant makes one in `_prepare` (and the lab's in lab_agent.py); `_drive` runs the turn on it."""

    system: str
    messages: list[Message]
    meta: dict[str, Any]
    mode: str
    specs: list[Any]
    run_tool: Any  # async (name, args) -> tools.ToolResult
    label_for: Any  # (name, args) -> str
    gctx: gate.GateContext
    max_steps: int
    max_tokens: int
    # async () -> None, after the reply is saved
    on_changed: Any = None


async def _prepare(turn: Turn, user_msg_id: int) -> Bench:
    reply_id = turn.reply_id
    state: dict[str, Any] = {"pre": None}
    async with SessionLocal() as db:
        user_msg = await db.get(ChatMessage, user_msg_id)
        session = await db.get(ChatSession, user_msg.session_id)
        course = await db.get(Course, session.course_id)
        scope = user_msg.scope or {}
        mode = scope.get("mode") if scope.get("mode") in MODE_NOTES else "ask"
        chapter = await db.get(Chapter, int(scope["chapter_id"])) if scope.get("chapter_id") else None
        if chapter is not None and chapter.course_id != course.id:
            chapter = None
        ai = await settings_svc.get_section(db, "ai")
        nonce = gate.new_nonce()
        ctx = tools.ToolContext(course_id=course.id, nonce=nonce, mode=mode)
        system = await _system(db, ctx, course, mode)
        messages = await _history(db, session.id, user_msg.id)
        first_chapter = (await projects.chapters_of(db, course.id))[:1]
        meta = {"agent": True, "mode": mode, "user_message": user_msg.content, "selection": scope.get("selection"),
                "chapter_path": chapter.path if chapter else None, "first_chapter_path": first_chapter[0].path if first_chapter else None}
        attachments = await _attachments(db, ctx, scope)
        messages.append(Message(role="user", parts=_user_parts(ctx, user_msg.content, scope, chapter, attachments)))
        course_id, session_id = course.id, session.id

    async def before_write(db: AsyncSession, course: Course) -> None:
        if state["pre"] is None:
            state["pre"] = await snapshot_state(db, course)

    async def after_write() -> None:
        async with SessionLocal() as db:
            course = await db.get(Course, course_id)
            cur = await snapshot_state(db, course)
        summary = summarize(state["pre"], cur)
        state["cur"] = cur
        await _save(reply_id, change={"status": "applied", **summary, "pre": state["pre"], "post": {
            "files": {p: v["blob"] for p, v in cur["files"].items()}, "chapters": cur["chapters"], "preamble": cur["preamble"]}})
        await turn.emit("change", {"files": summary["files"]})

    async def on_changed() -> None:
        if state["pre"] is not None and state.get("cur") is not None:
            focus = next((f["path"] for f in summarize(state["pre"], state["cur"])["files"] if f["path"].startswith("chapters/")), None)
            prop.recompile_later(course_id, focus)

    ctx.before_write, ctx.after_write = before_write, after_write
    return Bench(
        system=system, messages=messages, meta=meta, mode=mode, specs=tools.specs_for(mode),
        run_tool=lambda name, args: tools.run_tool(ctx, name, args), label_for=tools.label_for,
        gctx=gate.GateContext(chat_session_id=session_id, course_id=course_id, title="Assistente"),
        max_steps=ai.agent_max_steps, max_tokens=ai.max_output_tokens, on_changed=on_changed,
    )


async def _run(turn: Turn, user_msg_id: int) -> None:
    await _drive(turn, lambda: _prepare(turn, user_msg_id))


async def _drive(turn: Turn, prepare: Any) -> None:
    """Run a turn: ask the model, run the tools it calls, stream text and activity, save the reply, until it answers."""
    reply_id = turn.reply_id
    text_total = ""
    steps: list[dict[str, Any]] = []
    tokens_in = tokens_out = 0
    cost = 0.0
    try:
        bench: Bench = await prepare()
        messages, meta, mode, max_steps = bench.messages, bench.meta, bench.mode, bench.max_steps
        finished_cleanly = False

        for step in range(max_steps):
            if turn.cancel:
                break
            _prune(messages)
            req = EgressRequest(role="chat", task="chat.agent", request_key=f"agent:{reply_id}:{step}", system=bench.system, messages=list(messages),
                                max_tokens=bench.max_tokens, temperature=0.3, tools=bench.specs, meta={**meta, "step": step})
            step_text, result = "", None
            sep = "\n\n" if text_total.strip() and not text_total.endswith("\n\n") else ""
            for attempt in range(3):
                step_text, result = "", None
                try:
                    stream = await gate.stream(req, bench.gctx)
                    async for chunk in stream:
                        if isinstance(chunk, EgressResult):
                            result = chunk
                        else:
                            if not step_text and sep and chunk.strip():
                                await turn.emit("text", {"delta": sep})
                            step_text += chunk
                            await turn.emit("text", {"delta": chunk})
                            if turn.cancel:
                                break
                    break
                except gate.GateSendError as e:
                    if e.kind in RETRY_KINDS and attempt < 2 and not step_text:
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                    raise
            if result is None:
                break  # cancelled while streaming
            tokens_in += result.usage.tokens_in
            tokens_out += result.usage.tokens_out
            cost += result.cost_usd
            if step_text.strip():
                text_total += sep + step_text
            calls = result.tool_calls
            if not calls:
                if not step_text.strip() and result.finish_reason == "length":
                    raise gate.GateSendError("risposta interrotta dal limite di output prima di scrivere qualcosa", kind="truncated")
                finished_cleanly = True
                break
            # The model asked for tools: run them, then let it continue.
            a_parts = ([Part(type="text", text=step_text)] if step_text.strip() else []) + [
                Part(type="tool_call", tool_id=c.id, tool_name=c.name, tool_args=c.args, tool_extra=c.extra or None) for c in calls]
            messages.append(Message(role="assistant", parts=a_parts))
            r_parts: list[Part] = []
            images: list[Part] = []
            truncated = result.finish_reason == "length"
            for c in calls:
                sid = f"{step}-{c.id}"[:60]
                label = bench.label_for(c.name, c.args)
                entry = {"id": sid, "name": c.name, "label": label, "status": "running", "summary": ""}
                steps.append(entry)
                await turn.emit("tool", {"id": sid, "name": c.name, "label": label, "status": "running"})
                if truncated:
                    res = tools.ToolResult("Error: your reply was cut off at the output limit, so this call may be incomplete. "
                                           "Retry with smaller edits (edit_file) instead of rewriting whole files.", "risposta interrotta", ok=False)
                elif turn.cancel:
                    res = tools.ToolResult("Cancelled by the user.", "annullato", ok=False)
                else:
                    res = await bench.run_tool(c.name, c.args)
                entry["status"], entry["summary"] = ("ok" if res.ok else "error"), res.summary
                await turn.emit("tool_done", {"id": sid, "ok": res.ok, "summary": res.summary})
                r_parts.append(Part(type="tool_result", tool_id=c.id, tool_name=c.name, text=res.text, is_error=not res.ok))
                for im in res.images:
                    images.append(Part(type="image", blob=im["blob"], mime=im["mime"], width=im["width"], height=im["height"], label=im["label"]))
            messages.append(Message(role="user", parts=r_parts + images))
            await _save(reply_id, content=text_total, steps=steps, tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost)
        else:
            text_total += ("\n\n" if text_total else "") + f"(Mi sono fermato dopo {max_steps} passaggi: dimmi se devo continuare.)"
            finished_cleanly = True

        fields: dict[str, Any] = {"content": text_total, "steps": steps, "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost,
                                  "status": "done" if finished_cleanly and not turn.cancel else "cancelled",
                                  "suggestions": parse_suggestions(text_total), "review": parse_review(text_total) if mode == "review" else None}
        await _save(reply_id, **fields)
        if bench.on_changed is not None:
            await bench.on_changed()
        async with SessionLocal() as db:
            final = await db.get(ChatMessage, reply_id)
            await turn.emit("done", {"reply": message_out(final)})
    except Exception as e:  # noqa: BLE001
        if not isinstance(e, gate.GateSendError | gate.GateRefused):
            log.exception("agent turn failed")
        msg = str(e)[:1000] or e.__class__.__name__
        try:
            await _save(reply_id, status="error", error=msg, content=text_total, steps=steps, tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost)
            async with SessionLocal() as db:
                final = await db.get(ChatMessage, reply_id)
                await turn.emit("error", {"message": msg, "reply": message_out(final)})
        except Exception:  # noqa: BLE001
            log.exception("could not record the failure of an agent turn")
    finally:
        await turn.finish()


_ = (ToolCall, image_mime)
