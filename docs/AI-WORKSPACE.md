# AI workspace: API contract

Lecta is no longer a LaTeX editor. The user reads a **draft** of the document and works on it with an
**AI assistant** that can read and change everything in the course. LaTeX stays the storage format (public PDF,
`.tex` download) but nobody edits it by hand in the UI.

All endpoints below are admin-only (`/api`, cookie + CSRF like the rest) unless stated otherwise.

## Draft typeset by LaTeX (blocks as SVG)

`GET /api/courses/{cid}/chapters/{chid}/draft` (`GET /api/courses/{cid}/draft`: `{"chapters": [...]}` for all)

```json
{
  "chapter": {"id": 3, "title": "Campionamento", "path": "chapters/03-campionamento.tex", "position": 3},
  "width": 455.24,
  "blocks": [
    {"start": 1, "end": 1, "heading": "chapter", "id": "ch3-l1", "error": null,
     "pages": [{"url": "/api/courses/7/draft/svg/<variant>-0.svg", "w": 458.2, "h": 61.3, "x": -1.5, "raster": false}],
     "src": "\\chapter{Campionamento}"},
    {"start": 5, "end": 9, "error": "riga 7: Undefined control sequence.", "pages": [...], "src": "…"}
  ],
  "toc": [{"id": "ch3-l3", "title": "Il teorema", "level": 1, "line": 3}],
  "warnings": [],
  "typeset": 1,
  "took_ms": 640
}
```

* The chapter is split into **blocks** (`services/blocks.py`): paragraphs, headings, whole environments; `start`/`end`
  are its source lines. A block is what gets selected and flashed.
* Each block is typeset by the real LaTeX with the course's own preamble (`services/draft.py`), one page or more per
  block, cropped to the ink: `pages` are SVG pictures, sizes in bp; `width` is the text width, `x` where the picture
  starts relative to the text's left edge (negative when it reaches into the margin). Show a picture at
  `w / width` of the column, shifted by `x / width`.
* Typeset blocks are cached by their text, what they include and the counters they start from: only the blocks that
  changed (and the numbered ones they renumber) are typeset again; `typeset` says how many. A block that failed has
  `error` (with the source line); the other blocks are still shown. `src` is the block's LaTeX (what a selection
  sends), `raster` says a picture holds a photo (the dark theme inverts only the others).
* With pdflatex the run starts from the course preamble precompiled once (mylatexformat, remade when the preamble
  changes): an edit of one block typesets in well under a second even with TikZ, pgfplots and tcolorbox loaded.
* `GET /api/courses/{cid}/draft/svg/{name}`: a picture (`image/svg+xml`, immutable, strict CSP).

## The assistant (chat sessions with tools)

The assistant is an agent: it lists/reads/searches every file and source of the course, and writes, creates,
renames and deletes files and chapters, edits `main.tex` and the preamble, and checks the build. Its changes are
**applied immediately**; every turn can be **undone** with one click. There is no approval step.

Sessions (unchanged paths):
* `GET  /api/chat/sessions?course_id=` → list.
* `POST /api/chat/sessions` `{course_id, chapter_id?}` → session.
* `GET  /api/chat/sessions/{sid}` → `{id, course_id, title, messages: [Message]}`.

Send a message (starts a turn in the background, returns at once):

`POST /api/chat/sessions/{sid}/messages`
```json
{
  "content": "Spiegami meglio questo passaggio",
  "scope": {
    "chapter_id": 3,
    "selection": {"from_line": 12, "to_line": 14, "text": "the LaTeX of the picked blocks"},
    "mode": "ask"
  }
}
```
* `scope.mode`: `"ask"` (default; the assistant decides whether to edit), `"edit"` (do the change),
  `"explain"` (answer only, no edits), `"review"` (document feedback, no edits).
* `scope.selection` and `scope.chapter_id` are optional; without them the assistant sees the whole course outline.
* `scope.attachments`: `[{source_file_id, page?}]` optional.
* → `{"message": Message (user), "reply": Message (assistant, status "streaming")}`; 409 `{detail}` when no model is set
  for the “chat” role (Settings → Models), or when another turn is running for the course.

Stream the reply (Server-Sent Events; replays from the start, or after `?after=<seq>`; reconnecting is safe):

`GET /api/chat/replies/{reply_id}/events`

| event | data |
|-------|------|
| `text` | `{"delta": "…"}` assistant text (markdown) |
| `tool` | `{"id": "t1", "name": "read_file", "label": "Legge chapters/03-….tex", "status": "running"}` |
| `tool_done` | `{"id": "t1", "ok": true, "summary": "212 righe"}` |
| `change` | `{"files": [ChangedFile]}` cumulative, after each write: refresh the draft |
| `done` | `{"reply": Message}` final |
| `error` | `{"message": "…"}` final |

Other calls:
* `POST /api/chat/replies/{reply_id}/cancel` → stops the turn (edits already made stay; the turn can still be undone).
* `POST /api/chat/replies/{reply_id}/undo` → restores what that turn changed → `{"ok": true, "restored": [paths], "skipped": [paths changed since]}`.
* `GET  /api/courses/{cid}/review/latest` → `{"review": Review | null, "message_id": 12, "at": "…"}`.

`Message`:
```json
{
  "id": 41, "role": "assistant", "status": "streaming|done|error|cancelled",
  "content": "markdown text (suggestions/review blocks removed)",
  "scope": {}, "steps": [{"id": "t1", "name": "edit_file", "label": "Modifica chapters/03-….tex", "status": "ok|error|running", "summary": "…"}],
  "change": null,
  "suggestions": ["Aggiungi un esempio numerico", "…"],
  "review": null,
  "error": null, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "created_at": "…"
}
```
* `change` (when the turn wrote something): `{"status": "applied|undone", "files": [ChangedFile], "chapters": [{"id", "title", "op": "created|renamed|deleted|moved"}]}`.
* `ChangedFile`: `{"path": "chapters/03-….tex", "chapter_id": 3|null, "op": "modify|create|delete|rename", "added": 12, "removed": 3, "hunks": [{"from_line": 40, "to_line": 52}]}`.
  `hunks` are line ranges in the **new** text: highlight the draft blocks they overlap for a few seconds.
* `suggestions`: short follow-up prompts to show as chips under the reply.
* `review` (mode `"review"`): `{"verdict": "…", "score": 7, "strengths": ["…"], "issues": [{"chapter": "Campionamento", "chapter_id": 3, "text": "…", "fix": "prompt to send to fix it"}]}`.
  Show as a feedback card; each issue has a “Correggi” button that sends `fix` with `mode: "edit"`.

## Downloads

* `GET /api/courses/{cid}/source.zip` → the LaTeX project (main.tex, preamble.tex, chapters/, images/, figures/,
  README with how to compile). Working version.
* `POST /api/courses/{cid}/compile` `{"full": true}` (existing) then `pdf_url` → PDF of the working version, on demand.
* Public: the public course JSON gains `source_url` (`/api/public/courses/{slug}/source.zip`) and `source_size`
  (null when the course was published before this feature until its next rebuild). The PDF links are unchanged.

## Removed from the UI

The CodeMirror editor, the file tree, diagnostics, SyncTeX, the inline diff bar and the old “Chat” panel. The
endpoints for file content still exist (used by tests), but no page uses them.
