"""Fake provider: deterministic, valid outputs for every task, so every pipeline
can run end to end in tests and development without calling anyone.

It never performs network I/O. It reads the request's local-only `meta`
(structured hints the pipelines attach) plus the text parts.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

from .. import transport
from .base import Adapter, Completion, Prepared, PreparedCall, ProviderConfig

_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def esc(s: str) -> str:
    return "".join(_SPECIALS.get(c, c) for c in s)


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-zàèéìòù]{4,}", s.lower())}


def text_to_latex(text: str) -> str:
    """Markdown-ish plain text → LaTeX paragraphs, keeping $math$ intact."""
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        b = block.strip()
        if not b:
            continue
        heading = re.match(r"^#{1,6}\s+(.*)", b)
        if heading:
            out.append("\\subsection{" + esc(heading.group(1).strip()) + "}")
            continue
        # Keep math segments verbatim, escape the rest.
        segs = re.split(r"(\$\$.*?\$\$|\$[^$]+\$)", b, flags=re.S)
        para = "".join(s if s.startswith("$") else esc(s) for s in segs)
        para = re.sub(r"^\s*[-*]\s+", "", para, flags=re.M)
        out.append(para)
    return "\n\n".join(out)


class FakeAdapter(Adapter):
    type = "fake"
    label = "Fake (deterministico, per test e sviluppo)"
    default_base_url = "fake://local"
    needs_key = False

    async def complete(self, cfg: ProviderConfig, req: Prepared) -> Completion:
        if req.task == "chat.agent":
            return self._agent(req)
        text = self.respond(req)
        prompt_chars = len(req.system) + sum(len(p.text or "") for _, ps in req.messages for p in ps)
        images = sum(1 for _, ps in req.messages for p in ps if p.type == "image")
        pages = req.meta.get("pages") or []
        # Test hook: a unit of several pages containing FAKE:TRUNCATE hits the output limit.
        if len(pages) > 1 and any("FAKE:TRUNCATE" in (pg.get("text") or "") for pg in pages):
            return Completion(text[: len(text) // 2], prompt_chars // 4 + images * 800, req.max_tokens, finish_reason="length")
        # …and notes with FAKE:TRUNCATE and more than one heading hit it while writing the text.
        notes = req.meta.get("notes") or "" if req.task == "notes.compose" else ""
        if "FAKE:TRUNCATE" in notes and len(re.findall(r"^#{1,6}\s", notes, re.M)) > 1:
            return Completion(text[: len(text) // 2], prompt_chars // 4, req.max_tokens, finish_reason="length")
        return Completion(text, prompt_chars // 4 + images * 800, len(text) // 4, finish_reason="stop")

    async def stream(self, cfg: ProviderConfig, req: Prepared) -> AsyncIterator[str | Completion]:
        c = await self.complete(cfg, req)
        for i in range(0, len(c.text), 24):
            yield c.text[i : i + 24]
        yield c

    async def list_models(self, cfg: ProviderConfig) -> list[dict[str, Any]]:
        return [
            {"id": "fake-large", "label": "Fake large (vision)", "vision": True},
            {"id": "fake-small", "label": "Fake small", "vision": True},
        ]

    # ------------------------------------------------------------------ tasks

    def respond(self, req: Prepared) -> str:
        m = req.meta
        task = req.task
        if task == "read.pages":
            return self._read_pages(m)
        if task == "read.handwriting":
            return self._read_handwriting(m)
        if task == "notes.compose":
            return self._compose(m)
        if task == "notes.fix":
            return self._fix(m)
        if task == "classify.place":
            return json.dumps(self._place(m))
        if task == "provider.test":
            return "pong"
        return json.dumps({"ok": True, "task": task})

    @staticmethod
    def _page_latex(text: str, title: str | None) -> str:
        lines = []
        for ln in text.split("\n"):
            t = ln.strip()
            if t.startswith("# ") and title and t[2:].strip() == title:
                continue
            lines.append(ln)
        out = []
        for block in re.split(r"(\[\[IMG [^\]]+\]\])", "\n".join(lines)):
            mm = re.match(r"\[\[IMG ([^\]]+)\]\]", block)
            if mm:
                out.append(f"\\lectaimage[Figura]{{{mm.group(1).strip()}}}")
            elif block.strip():
                out.append(text_to_latex(block))
        body = "\n\n".join(out)
        if "FAKE:BADLATEX" in text:
            body += "\n\n\\undefinedmacro{}"
        return body

    def _read_pages(self, m: dict[str, Any]) -> str:
        pages = m.get("pages") or []
        head = ["%%TITLE: " + (next((p.get("title") for p in pages if p.get("title")), None) or m.get("label") or "Appunti")]
        parts: list[str] = []
        current = None
        for pg in pages:
            title = (pg.get("title") or "").strip()
            if title and title != current:
                parts.append("\\section{" + esc(title) + "}")
                current = title
            text = (pg.get("text") or "").strip()
            if text:
                parts.append(self._page_latex(text, title))
            elif pg.get("has_image"):
                fid = f"p{pg['page']}n1"
                head.append(f"%%FIGURE {fid} {pg['page']} 0.1 0.55 0.9 0.8")
                parts.append(f"La relazione $y = f(x)$ descrive il sistema.\n\n\\lectaimage[Schema]{{{fid}}}")
        if not parts:
            parts = ["\\review{Nessun contenuto riconosciuto.}"]
        return "\n".join(head) + "\n" + "\n\n".join(parts) + "\n%%END\n"

    def _read_handwriting(self, m: dict[str, Any]) -> str:
        label = esc(m.get("label") or "pagina")
        return (
            "%%TITLE: Appunti a mano\n%%FIGURE p1n1 1 0.1 0.6 0.9 0.9\n"
            f"Appunti manoscritti ({label}): definizione di sistema e relativo esempio. La relazione $y = f(x)$ descrive il sistema.\n\n"
            "\\lectaimage[Schema]{p1n1}\n%%END\n"
        )

    @staticmethod
    def _notes_latex(notes: str) -> str:
        """Bullet notes → paragraphs (headings → sections, code → verbatim, [[IMG id]] → \\lectaimage)."""
        out = []
        for n, seg in enumerate(re.split(r"^```[a-z]*\n(.*?)^```\s*$", notes, flags=re.M | re.S)):
            if n % 2:
                out.append("\\begin{verbatim}\n" + seg.rstrip("\n") + "\n\\end{verbatim}")
                continue
            seg = re.sub(r"^#\s+.*$", "", seg, count=1, flags=re.M) if n == 0 else seg
            for block in re.split(r"(\[\[IMG [^\]]+\]\])", seg):
                mm = re.match(r"\[\[IMG ([^\]]+)\]\]", block)
                if mm:
                    out.append(f"\\lectaimage[Figura]{{{mm.group(1).strip()}}}")
                elif block.strip():
                    out.append(text_to_latex(block).replace("\\subsection{", "\\section{"))
        return "\n\n".join(out)

    @staticmethod
    def _material_prose(material: str) -> str:
        """The material with its lists turned into running text."""
        lines = []
        for ln in material.split("\n"):
            s = ln.strip()
            if re.match(r"^\\(begin|end)\{(itemize|enumerate)\}$", s):
                continue
            lines.append(re.sub(r"^\s*\\item\s*", "", ln))
        return "\n".join(lines).strip()

    def _compose(self, m: dict[str, Any]) -> str:
        notes, material = m.get("notes") or "", m.get("material") or ""
        head = [f"%%TITLE: {m.get('title') or 'Appunti'}"] if (m.get("part") or 1) == 1 else []
        parts = []
        if notes.strip():
            parts.append(self._notes_latex(notes))
        if material.strip():
            parts.append(("\\section{Dalle slide}\n" if notes.strip() else "") + self._material_prose(material))
        return "\n".join(head) + "\n" + "\n\n".join(parts) + "\n%%END\n"

    def _fix(self, m: dict[str, Any]) -> str:
        latex = m.get("latex") or ""
        bad = set(m.get("error_lines") or [])
        lines = latex.split("\n")
        fixed = [("% removed by auto-fix: " + ln) if (i + 1) in bad else ln for i, ln in enumerate(lines)]
        out = "\n".join(fixed).replace("\\undefinedmacro{}", "")
        return out + "\n%%END\n"

    def _place(self, m: dict[str, Any]) -> dict[str, Any]:
        text_words = _words(m.get("group_text") or "") | _words(m.get("group_title") or "")
        best = None
        for c in m.get("candidates") or []:
            cw = _words(" ".join([c.get("course_name", ""), c.get("chapter_title") or "", " ".join(c.get("outline") or [])]))
            overlap = len(text_words & cw) / max(1, min(len(cw), 12))
            score = min(0.99, 0.2 + 1.5 * float(c.get("score") or 0) + 0.5 * overlap)
            if best is None or score > best[0]:
                best = (score, c)
        if best is None:
            return {"placements": [], "rationale": "no candidates"}
        score, c = best
        placement = {
            "course_id": c.get("course_id"),
            "chapter_id": c.get("chapter_id") if score >= 0.6 else None,
            "new_chapter_title": None if score >= 0.6 else (m.get("group_title") or "Nuovo capitolo"),
            "confidence": round(score, 3),
            "rationale": f"term overlap with “{c.get('chapter_title') or c.get('course_name')}”",
        }
        return {"placements": [placement]}

    # ------------------------------------------------------------------ the assistant (tool use)

    def _agent(self, req: Prepared) -> Completion:
        """A scripted assistant: reads the chapter, edits it when asked, answers. Deterministic, no network."""
        m = req.meta
        msg = (m.get("user_message") or "").strip()
        low = msg.lower()
        mode = m.get("mode") or "ask"
        role, parts = req.messages[-1]
        results = [p for p in parts if p.type == "tool_result"]
        prompt_chars = len(req.system) + sum(len(p.text or "") for _, ps in req.messages for p in ps)
        tin = prompt_chars // 4

        def done(text: str, calls: list[PreparedCall] | None = None) -> Completion:
            return Completion(text, tin, max(1, len(text) // 4), finish_reason="stop", tool_calls=calls or [])

        def call(name: str, **args: Any) -> PreparedCall:
            return PreparedCall(f"fake_{req.request_key}_{name}", name, args)

        if m.get("lab"):
            return self._lab_agent(req, msg, low, mode, results, done, call)
        # Test hooks.
        if "FAKE:LOOP" in msg:
            return done("Continuo a leggere. ", [call("course_overview")])
        if "FAKE:ERROR" in msg:
            raise transport.ProviderReplyError("bad_request", "fake provider failure")
        path = m.get("chapter_path") or m.get("first_chapter_path")
        chap_match = re.search(r"nuovo capitolo:\s*(.+)", msg, re.I)
        if not results:
            if chap_match and mode not in ("explain", "review"):
                return done("Creo il capitolo. ", [call("create_chapter", title=chap_match.group(1).strip()[:80],
                                                          content="\\section{Introduzione}\nTesto iniziale del capitolo.\n")])
            if mode == "review":
                return done("Leggo la struttura. ", [call("course_overview")])
            if not path:
                return done("Non ci sono ancora capitoli su cui lavorare: carica del materiale o chiedimi di creare un capitolo.")
            return done("Leggo il capitolo. ", [call("read_file", path=path)])
        last = results[-1]
        fenced = re.search(r"<<<UNTRUSTED-[0-9a-f]+>>>[^\n]*\n(.*)\n<<<END-[0-9a-f]+>>>", last.text or "", re.S)
        source = fenced.group(1) if fenced else ""
        suggestions = '\n\n```suggestions\n["Riassumi il capitolo", "Aggiungi un altro esempio"]\n```'
        if last.tool_name == "create_chapter":
            return done("Ho creato il capitolo con una prima sezione." + suggestions)
        if mode == "review":
            review = {"verdict": "Documento ben avviato ma da completare.", "score": 7, "strengths": ["Struttura chiara"],
                      "issues": [{"chapter_id": None, "chapter": "", "text": "Mancano esempi numerici.", "fix": "Aggiungi un esempio numerico a ogni definizione."}]}
            return done("Ho letto il documento: la struttura è chiara, mancano alcuni esempi.\n\n```review\n" + json.dumps(review, ensure_ascii=False) + "\n```")
        if last.tool_name == "read_file" and source and mode != "explain" and (mode == "edit" or "esempio" in low or "aggiungi" in low):
            anchor = "\\end{definition}" if "\\end{definition}" in source else next(
                (ln for ln in reversed(source.split("\n")) if ln.strip()), "")
            if anchor:
                block = "\\begin{example}\nSia $x = 1$: allora $x^2 = 1$.\n\\end{example}"
                return done("Aggiungo un esempio. ", [call("edit_file", path=path, search=anchor, replace=anchor + "\n\n" + block)])
        if last.tool_name == "edit_file":
            if last.is_error:
                return done("Non sono riuscito a modificare il file: " + (last.text or "")[:120])
            return done("Ho aggiunto un esempio dopo la definizione." + suggestions)
        sel = (m.get("selection") or {}).get("text")
        if sel:
            return done(f"Spiegazione di «{sel[:80]}»: è il passaggio che il capitolo usa per introdurre l'idea; te lo riformulo con parole semplici." + suggestions)
        return done("Ho letto il capitolo: nulla da cambiare." + suggestions)

    def _lab_agent(self, req: Prepared, msg: str, low: str, mode: str, results: list[Any], done: Any, call: Any) -> Completion:
        """The lab assistant, scripted: reads the open file; edits it, writes a new file or comments it when asked."""
        path = req.meta.get("file_path")
        if not results:
            if not path:
                return done("Il laboratorio non ha ancora file di testo: caricane uno.", [])
            return done("Leggo il file. ", [call("read_lab_file", path=path)])
        last = results[-1]
        suggestions = '\n\n```suggestions\n["Spiega la riga successiva", "Commenta il ciclo"]\n```'
        if last.tool_name == "read_lab_file" and mode != "explain":
            fenced = re.search(r"<<<UNTRUSTED-[0-9a-f]+>>>[^\n]*\n(.*)\n<<<END-[0-9a-f]+>>>", last.text or "", re.S)
            lines = [re.sub(r"^\s*\d+\| ", "", ln) for ln in (fenced.group(1) if fenced else "").split("\n")]
            first = next((ln for ln in lines if ln.strip()), "")
            if "commenta" in low:
                return done("Aggiungo un commento. ", [call("add_comment", path=path, from_line=1, to_line=1, body="Qui inizia il programma: è la prima riga.")])
            if "soluzione" in low:
                return done("Scrivo la soluzione. ", [call("write_lab_file", path="soluzione.txt", content="La soluzione dell'esercizio.\n")])
            if first and (mode == "edit" or "aggiungi" in low or "modifica" in low):
                return done("Modifico il file. ", [call("edit_lab_file", path=path, search=first, replace="// Modificato dall'assistente\n" + first)])
        if last.tool_name in ("edit_lab_file", "write_lab_file", "add_comment"):
            if last.is_error:
                return done("Non ci sono riuscito: " + (last.text or "")[:120])
            what = {"edit_lab_file": "Ho modificato il file", "write_lab_file": "Ho scritto la soluzione in soluzione.txt", "add_comment": "Ho commentato la prima riga"}[last.tool_name]
            return done(what + "." + suggestions)
        sel = (req.meta.get("selection") or {}).get("text")
        if sel:
            return done(f"Queste righe («{sel[:60]}») fanno il lavoro principale del programma: te le spiego passo per passo." + suggestions)
        return done("Ho letto il file: è un programma di esempio del laboratorio." + suggestions)
