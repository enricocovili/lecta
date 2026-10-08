"""The lesson assistant: the course assistant's turn (agent._drive) on a lesson, to explain what a slide says. It only answers:
nothing in the course or the lesson is changed (the student brings an answer into the page's notes from the editor).

Every question comes with the whole lesson in the prompt (each page's slide text, typed notes, whether it carries
handwriting) and the pictures of the pages from ten before to ten after the one asked about, with the strokes drawn on them
(the asked page at full size), and a sharp cut of the area the user picked on it, if any; the model looks at any other page
with view_page and reads the lesson's chapter.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from ..db import SessionLocal
from ..egress import gate
from ..egress.schemas import Message, Part
from ..models import ChatMessage, ChatSession, Course
from ..services import prompts
from ..services import settings as settings_svc
from . import agent
from . import lesson_tools as tools


def running_turn(lesson_id: int) -> agent.Turn | None:
    return next((t for t in agent.RUNS.values() if t.lesson_id == lesson_id and not t.finished), None)


def window(n: int, index: int, radius: int = tools.WINDOW) -> list[int]:
    """The pages that come with a question about pages[index] as pictures: `radius` on each side, within the lesson."""
    return list(range(max(0, index - radius), min(n, index + radius + 1)))


async def _user_parts(ctx: tools.LessonContext, lesson: Any, pages: list[dict[str, Any]], content: str, scope: dict[str, Any]) -> list[Part]:
    parts: list[Part] = []
    index = next((i for i, p in enumerate(pages) if p["id"] == scope.get("page_id")), None)
    if index is not None:
        near = window(len(pages), index)
        imgs = await tools.pictures(lesson, pages, near, [tools.ASKED_SIDE if i == index else tools.NEAR_SIDE for i in near])
        parts.append(Part(type="text", text=f"The user is asking about {tools.tag(pages, index)}. The pictures of pages {near[0] + 1}–{near[-1] + 1} "
                                            "follow, in order, each after its name; the student's handwriting and highlights are drawn on them."))
        for i, im in zip(near, imgs, strict=True):
            parts.append(Part(type="text", text=tools.tag(pages, i) + (" ← the page asked about" if i == index else "")))
            parts.append(Part(type="image", blob=im["blob"], mime=im["mime"], width=im["width"], height=im["height"], label=im["label"]))
        if scope.get("region"):
            im = await tools.area_picture(lesson, pages, index, scope["region"])
            parts.append(Part(type="text", text=f"The user picked this area of {tools.tag(pages, index)}: the question is about what it shows "
                                                "(read it in the context of the whole page)."))
            parts.append(Part(type="image", blob=im["blob"], mime=im["mime"], width=im["width"], height=im["height"], label=im["label"]))
    parts.append(Part(type="text", text=content))
    return parts


async def _prepare(turn: agent.Turn, user_msg_id: int) -> agent.Bench:
    async with SessionLocal() as db:
        user_msg = await db.get(ChatMessage, user_msg_id)
        session = await db.get(ChatSession, user_msg.session_id)
        course = await db.get(Course, session.course_id)
        scope = user_msg.scope or {}
        ai = await settings_svc.get_section(db, "ai")
        nonce = gate.new_nonce()
        base, _ = await prompts.render(db, "lesson.system", nonce)
        messages = await agent._history(db, session.id, user_msg.id)
        session_id, lesson_id, language = session.id, session.lesson_id, course.language
    ctx = tools.LessonContext(lesson_id=lesson_id, course_id=session.course_id, nonce=nonce)
    lesson, pages = await tools.load(lesson_id)
    index = next((i for i, p in enumerate(pages) if p["id"] == scope.get("page_id")), None)
    system = "\n\n".join([
        base,
        f"The whole lesson, page by page (at the start of this turn):\n{await tools.outline(ctx, lesson, pages)}",
        f"Language of the course: {language}. Today is {datetime.now(UTC).strftime('%Y-%m-%d')}.",
    ])
    messages.append(Message(role="user", parts=await _user_parts(ctx, lesson, pages, user_msg.content, scope)))
    meta = {"agent": True, "lesson": True, "mode": "explain", "user_message": user_msg.content,
            "page": tools.tag(pages, index) if index is not None else None,
            "pictures": [p.label for p in messages[-1].parts if p.type == "image"], "pages": len(pages),
            "area": next((f"{p.width}×{p.height}" for p in messages[-1].parts if p.type == "image" and (p.label or "").endswith("area picked")), None)}
    return agent.Bench(
        system=system, messages=messages, meta=meta, mode="explain", specs=tools.SPECS,
        run_tool=lambda name, args: tools.run_tool(ctx, name, args), label_for=tools.label_for,
        gctx=gate.GateContext(chat_session_id=session_id, course_id=ctx.course_id, title="Assistente della lezione"),
        max_steps=ai.agent_max_steps, max_tokens=ai.max_output_tokens,
    )


async def start_turn(session: ChatSession, user_msg: ChatMessage, reply: ChatMessage) -> agent.Turn:
    agent._purge()
    turn = agent.Turn(reply_id=reply.id, session_id=session.id, course_id=session.course_id, lesson_id=session.lesson_id)
    agent.RUNS[reply.id] = turn
    turn.task = asyncio.get_running_loop().create_task(agent._drive(turn, lambda: _prepare(turn, user_msg.id)))
    return turn
