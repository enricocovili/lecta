"""The AI assistant: turns with tools, streamed events, immediate changes and undo."""

from __future__ import annotations

import json

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditLog

from .test_ingest_e2e import setup_fake

CHAPTER = r"""\chapter{Gruppi}
\section{Definizione}
\begin{definition}[Gruppo]
Un gruppo è un insieme con un'operazione associativa, un elemento neutro e gli inversi.
\end{definition}
\section{Proprietà}
L'elemento neutro è unico.
"""


async def _course(admin, name="Algebra"):
    c = (await admin.post("/api/courses", json={"name": name, "language": "it", "chapters": ["Gruppi"]})).json()
    ch = c["chapters"][0]
    await admin.put(f"/api/courses/{c['id']}/files/content", json={"path": ch["path"], "content": CHAPTER})
    s = (await admin.post("/api/chat/sessions", json={"course_id": c["id"], "chapter_id": ch["id"]})).json()
    return c, ch, s


async def _events(admin, rid: int, after: int = 0) -> list[tuple[str, dict]]:
    out = []
    async with admin.stream("GET", f"/api/chat/replies/{rid}/events", params={"after": after}) as r:
        assert r.status_code == 200, await r.aread()
        ev = None
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:") and ev:
                out.append((ev, json.loads(line[5:])))
    return out


async def _ask(admin, s, ch, text, **scope):
    body = {"content": text, "scope": {"chapter_id": ch["id"], **scope}}
    r = await admin.post(f"/api/chat/sessions/{s['id']}/messages", json=body)
    assert r.status_code == 200, r.text
    reply = r.json()["reply"]
    assert reply["status"] == "streaming"
    return reply, await _events(admin, reply["id"])


async def _content(admin, c, ch) -> str:
    return (await admin.get(f"/api/courses/{c['id']}/files/content", params={"path": ch["path"]})).json()["content"]


async def _reply(admin, s, rid):
    return next(m for m in (await admin.get(f"/api/chat/sessions/{s['id']}")).json()["messages"] if m["id"] == rid)


async def test_turn_reads_edits_and_the_change_is_applied_at_once(admin):
    await setup_fake(admin)
    c, ch, s = await _course(admin)
    reply, events = await _ask(admin, s, ch, "Aggiungi un esempio dopo la definizione")
    kinds = [e for e, _ in events]
    assert kinds[-1] == "done" and kinds.count("text") > 1  # streamed in pieces
    tools = [d for e, d in events if e == "tool"]
    assert [t["name"] for t in tools] == ["read_file", "edit_file"] and tools[0]["label"].startswith("Legge chapters/")
    assert [d["ok"] for e, d in events if e == "tool_done"] == [True, True]
    # Applied without any approval.
    assert "\\begin{example}" in await _content(admin, c, ch)
    change = next(d for e, d in events if e == "change")
    f = change["files"][0]
    assert f["path"] == ch["path"] and f["op"] == "modify" and f["added"] >= 3 and f["hunks"] and f["chapter_id"] == ch["id"]

    final = next(d for e, d in events if e == "done")["reply"]
    assert final["status"] == "done" and "esempio" in final["content"] and "```" not in final["content"]
    assert final["suggestions"] == ["Riassumi il capitolo", "Aggiungi un altro esempio"]
    assert final["change"]["status"] == "applied" and "pre" not in final["change"] and len(final["steps"]) == 2
    async with SessionLocal() as db:
        row = (await db.execute(select(AuditLog).where(AuditLog.request_key == f"agent:{reply['id']}:0"))).scalar_one()
        assert row.chat_session_id == s["id"] and row.status == "ok"
    # A reconnecting browser replays the same events.
    again = await _events(admin, reply["id"])
    assert [e for e, _ in again] == kinds
    assert [e for e, _ in await _events(admin, reply["id"], after=len(kinds) - 1)] == ["done"]


async def test_undo_restores_the_turn_and_only_once(admin):
    await setup_fake(admin)
    c, ch, s = await _course(admin, "Algebra undo")
    reply, _ = await _ask(admin, s, ch, "Aggiungi un esempio")
    assert "\\begin{example}" in await _content(admin, c, ch)
    r = await admin.post(f"/api/chat/replies/{reply['id']}/undo")
    assert r.status_code == 200 and r.json()["restored"] == [ch["path"]]
    assert await _content(admin, c, ch) == CHAPTER
    assert (await _reply(admin, s, reply["id"]))["change"]["status"] == "undone"
    assert (await admin.post(f"/api/chat/replies/{reply['id']}/undo")).status_code == 409


async def test_undo_is_refused_when_the_file_changed_afterwards(admin):
    await setup_fake(admin)
    c, ch, s = await _course(admin, "Algebra later")
    reply, _ = await _ask(admin, s, ch, "Aggiungi un esempio")
    edited = (await _content(admin, c, ch)) + "\nAltro testo.\n"
    await admin.put(f"/api/courses/{c['id']}/files/content", json={"path": ch["path"], "content": edited})
    r = await admin.post(f"/api/chat/replies/{reply['id']}/undo")
    assert r.status_code == 409 and r.json()["detail"]["skipped"] == [ch["path"]]
    assert await _content(admin, c, ch) == edited


async def test_explain_mode_never_writes(admin):
    await setup_fake(admin)
    c, ch, s = await _course(admin, "Algebra explain")
    sel = {"from_line": 4, "to_line": 4, "text": "elemento neutro"}
    _, events = await _ask(admin, s, ch, "Aggiungi un esempio: spiegami questo", mode="explain", selection=sel)
    assert not [d for e, d in events if e == "tool" and d["name"] in ("edit_file", "write_file")]
    assert not any(e == "change" for e, _ in events)
    assert "elemento neutro" in next(d for e, d in events if e == "done")["reply"]["content"]
    assert await _content(admin, c, ch) == CHAPTER


async def test_the_assistant_can_create_a_chapter_and_undo_removes_it(admin):
    await setup_fake(admin)
    c, ch, s = await _course(admin, "Algebra new")
    reply, events = await _ask(admin, s, ch, "nuovo capitolo: Anelli")
    final = next(d for e, d in events if e == "done")["reply"]
    assert final["change"]["chapters"] == [{"id": final["change"]["chapters"][0]["id"], "title": "Anelli", "op": "created"}]
    course = (await admin.get(f"/api/courses/{c['id']}")).json()
    assert [x["title"] for x in course["chapters"]] == ["Gruppi", "Anelli"]
    new = course["chapters"][1]
    assert "Testo iniziale" in await _content(admin, c, new)
    main = (await admin.get(f"/api/courses/{c['id']}/files/content", params={"path": "main.tex"})).json()["content"]
    assert new["path"].removesuffix(".tex") in main
    assert (await admin.post(f"/api/chat/replies/{reply['id']}/undo")).status_code == 200
    course = (await admin.get(f"/api/courses/{c['id']}")).json()
    assert [x["title"] for x in course["chapters"]] == ["Gruppi"]
    main = (await admin.get(f"/api/courses/{c['id']}/files/content", params={"path": "main.tex"})).json()["content"]
    assert new["path"].removesuffix(".tex") not in main


async def test_review_mode_returns_feedback_and_it_is_the_latest(admin):
    await setup_fake(admin)
    c, ch, s = await _course(admin, "Algebra review")
    assert (await admin.get(f"/api/courses/{c['id']}/review/latest")).json()["review"] is None
    _, events = await _ask(admin, s, ch, "Valuta il documento", mode="review")
    final = next(d for e, d in events if e == "done")["reply"]
    assert final["review"]["score"] == 7 and final["review"]["issues"][0]["fix"] and "```" not in final["content"]
    assert not any(e == "change" for e, _ in events)
    latest = (await admin.get(f"/api/courses/{c['id']}/review/latest")).json()
    assert latest["review"]["verdict"] and latest["message_id"] == final["id"]


async def test_step_limit_errors_and_one_turn_at_a_time(admin):
    await setup_fake(admin)
    c, ch, s = await _course(admin, "Algebra limits")
    assert (await admin.put("/api/settings/ai", json={"agent_max_steps": 5})).status_code == 200
    try:
        reply, events = await _ask(admin, s, ch, "FAKE:LOOP")
        final = next(d for e, d in events if e == "done")["reply"]
        assert len(final["steps"]) == 5 and "5 passaggi" in final["content"]
        reply, events = await _ask(admin, s, ch, "FAKE:ERROR")
        assert events[-1][0] == "error" and "fake provider failure" in events[-1][1]["message"]
        assert (await _reply(admin, s, reply["id"]))["status"] == "error"
    finally:
        await admin.put("/api/settings/ai", json={"agent_max_steps": 40})


async def test_no_model_assigned_is_a_clear_409(admin):
    c = (await admin.post("/api/courses", json={"name": "Senza modello", "chapters": ["Uno"]})).json()
    s = (await admin.post("/api/chat/sessions", json={"course_id": c["id"]})).json()
    await admin.put("/api/settings/roles", json={"chat": {"primary": {"provider_id": None, "model": None}}})
    r = await admin.post(f"/api/chat/sessions/{s['id']}/messages", json={"content": "ciao"})
    assert r.status_code == 409 and "Models" in r.json()["detail"]
