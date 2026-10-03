# Status

All seven milestones are implemented, each committed after its build, tests and README update:

| # | Milestone | Commit |
|---|-----------|--------|
| 1 | Skeleton: compose + networks, Node server + `/api` proxy, FastAPI, Postgres/pgvector, job queue, setup wizard, auth, courses/chapters, folder view, homepages, public/private boundary | `Milestone 1` |
| 2 | LaTeX: compile service (incremental, figure cache, sandbox), editor + PDF preview + error navigation, revisions, publishing | `Milestone 2` |
| 3 | Providers, privacy gate (approvals, plans, audit, costs), fake provider | `Milestone 3` |
| 4 | Ingestion stages 1–4, diagrams → LaTeX, review queue with per-hunk diffs | `Milestone 4` |
| 5 | Hybrid retrieval with local embeddings (benchmark + lexical fallback), Uncategorized inbox, phone quick upload | `Milestone 5` |
| 6 | Review chat with streamed replies and diff proposals | `Milestone 6` |
| 7 | Search, export/backup, rate limiting, recovery CLI, mobile/privacy checks, docs | `Milestone 7` |

The stack is deployed on this server as the compose project `lecta` and has
**not been set up yet**: the admin account is yours to create. Get the setup code
with `docker compose logs backend | grep "setup code"` (or
`docker compose exec backend cat /data/secrets/setup-code`), open the site and
follow the wizard. No reverse-proxy configuration was created; point yours at
`http://lecta-frontend:4321` on `webnet`.

## Interface redesign (after milestone 7)

The whole frontend was redesigned after `docs/Lecta — nuova interfaccia.pdf`
(DECISIONS 51–56): new top bar / sidebar / phone tab bar, Home with activity list and
course cards, Mappa collegamenti (`/admin/map`, `GET /api/concepts`), the editor with
side-by-side AI review, the public landing page and PDF reader (document / presentation),
and every other page restyled and translated to Italian. New backend endpoints (additive):
`GET /api/concepts`, `GET /api/dashboard/counts`, `GET /api/public/courses.zip`; `/api/dashboard`
gains `activity` and `courses`, `/api/tree` gains per-course `status`, `sources`,
`pending_reviews`, public courses gain page ranges, and the site settings gain `byline`.
Verified with the backend suite (102 tests) and the browser suite (11 tests, labels now Italian).

## Autonomous platform (after the redesign)

Imports run by themselves and the platform has fewer moving parts (DECISIONS 57–65):

* **Import**: local extraction of text (with sub/superscripts), embedded images and
  vector drawings; pages go to a vision model only when text isn't enough; one
  parallel reading step writes the LaTeX (math included, pictures as images via
  `\lectaimage`); one compile check with at most one AI fix; written straight
  into the course (new chapter / appended) or to *Da smistare* when unsure. On the
  55-slide deck already on this server, extraction takes ~2.4 s: 12 overlay steps
  skipped, 28 pages read from text, 15 (display math) with their picture, 12 pictures.
* **Removed**: approvals and session grants, the privacy gate (ZDR/local flags,
  provenance, transport tickets, payload copies), the review queue, the revision
  history, figure regeneration, diagram → TikZ recreation, per-chapter publication
  flags and publication snapshots.
* **Chat**: edits appear as an inline diff in the editor; *Accetta* writes them.
* **Publishing**: ON/OFF; published courses are rebuilt ~2 min after the last change.
* **Study text** (DECISIONS 66–69): after the reading step, a writing step turns the
  class notes (`.md`/`.txt`) and the material into prose to study from, following
  the notes; without notes the material is summarised. The upload warns when there
  is no notes file (quick phone uploads excepted). Stepper: Estrazione › Lettura ›
  Stesura › Inserimento.
* **Settings → AI e costi**: disclaimer, request limits, costs and the call log.
* Migration `0008` (applied on this server; nothing pending was lost: there were no
  proposals, inbox items, chapters or publications).

Verified with the backend suite (**97 tests**: `scripts/test.sh`), the browser suite
(**11 tests**: `scripts/e2e.sh`) and `scripts/smoke.sh`. Not measured yet: the speed
and output quality of a real import with the configured provider (the fake provider
only checks the mechanics).

## AI workspace (after the autonomous platform)

The interface no longer edits LaTeX; it is built around the draft and the assistant (DECISIONS 70–77,
contract in `docs/AI-WORKSPACE.md`):

* **Draft**: every chapter typeset by LaTeX block by block (SVG, cached per block and counter state, DECISIONS 94),
  blocks mapped to source lines; full PDF on demand.
* **Assistant**: agent with tools over the whole course (files, `main.tex`, preamble, chapters, sources
  with page images), changes applied at once, **undo per turn**, background turns with replayable
  events, select-to-ask toolbar, document review with score and issues.
* **Downloads**: LaTeX source `.zip` (working and public), PDF.
* **Backend**: tool calling in the gateway and the three real adapters (request/reply mapping tested
  with stubbed transports; **not exercised against the real services**), migration `0009`.
* **Not measured**: quality, cost and latency of the assistant with real models; the fake provider only
  checks the mechanics.

## Lessons (after the AI workspace)

Taking notes during the lecture, and using them as the input of the text (DECISIONS 78–84, contract in
`docs/LESSONS.md`):

* **Lessons**: slides (PDF) with, per page, Markdown notes next to the slide and pen strokes over it (pen, highlighter,
  stroke eraser, undo/redo; mouse, stylus and finger with palm rejection), blank pages to write more, autosave that
  survives a lost connection, screen kept on, layouts for tablet and foldable laptop. New `/admin/lessons`.
* **Downloads**: PDF with the strokes drawn on it, the notes as Markdown.
* **Lesson → text**: the import takes the annotated slides (as pictures), the notes (a section per slide) and the handwritten
  pages; one lesson is one chapter; notes and slides stay paired all the way.
* **Lesson ↔ chapter**: a lesson remembers its chapter (`lessons.chapter_id`, migration `0013`) and its text sits between
  «lezione N» markers; changing the lesson and generating again updates that section in place, integrated with what is there
  (the previous version goes to the writing step), instead of a new chapter. See `docs/LESSONS.md`.
* The top bar of the course workspace no longer has the *Revisione AI* button.
* **Guidelines** per subject, asked at every generation (lesson, or upload into a chosen subject) with a notice when there are
  none; used by the writing step and by the assistant. Migration `0010`.
* Verified with the backend suite (**152 tests**: `DEV=1 scripts/test.sh`) and the browser suite (**23 tests**:
  `scripts/e2e.sh`, including drawing with the mouse, synthetic pen and touch events, the eraser, undo, autosave across a
  reload, the guidelines dialog and the phone layout).
* **Not measured**: quality of the text with the strokes as pictures on real models (the fake provider only checks the
  mechanics); the drawing feel on a real tablet/stylus (tested in the browser with mouse and synthetic pen/touch events).

## Renamed to Lecta

The app was called Appunti until 2026-10-02 (DECISIONS 91–92). What changed and where it stands:

* **Done and verified**: the name in the UI and prompts, the LaTeX macros and markers (`\lecta…`), cookies, storage keys,
  env vars (`LECTA_*`), the compose project / services / images / database / socket. Backend suite, the 29 browser tests
  and `scripts/smoke.sh --full` pass under the new names.
* **This server** moved with a one-off migration script (rehearsed first on a copy of the real data: both courses compile
  in draft and publish mode, the undo snapshots still match the files, nothing needed re-indexing). It now runs as the
  compose project `lecta`; the Caddy site `appunti.enricocovili.com` points at `lecta-frontend:4321`. The public host name
  is unchanged on purpose (passkeys are bound to it).
* **Cleaned up** (2026-10-02): the stopped `appunti-*` containers, images, networks and `appunti_*` volumes, the
  `appunti-before-lecta-*` backups and the older `appunti-preview*` stack are gone, so there is no way back to the old
  installation. The script is removed too (it is in the git history before the commit that deletes it). Only the public host name
  `appunti.enricocovili.com` keeps the old name.
* **Not renamed**: `docs/Lecta — nuova interfaccia.pdf` still shows the old name inside the prototype.

## Definition of done: how each point is verified

| Requirement | Evidence |
|-------------|----------|
| `docker compose up -d` (with `webnet` existing) starts everything | done on this server; all 5 services healthy |
| `lecta-frontend` reachable on `webnet` | `scripts/smoke.sh` (curl from a container on `webnet`) |
| no published ports, nothing else on `webnet` | `scripts/smoke.sh` (`docker inspect` of every container; the compile container has `network_mode: none`) |
| everything configured from the browser after the wizard | Settings covers account/2FA, providers, models, prompts, privacy, LaTeX, embeddings, categorisation, uploads, publishing, backup; `e2e/tests/02-providers.spec.ts` configures a provider and every role from the UI |
| anonymous users reach only published listings/PDFs | `tests/test_boundary.py`: every route × every method (with and without bodies, forged cookies) → 404; public endpoints only expose published snapshots; admin pages 404 in the browser (`e2e 01`) |
| mixed fixture zip → whole pipeline → compiling chapter | `tests/test_ingest_e2e.py::test_mixed_zip_into_new_course_writes_a_compiling_chapter` (extraction, parallel reading, 3 pictures as images, compile check, written directly, sources linked, full build ok), and the same flow in the browser (`e2e 03`) |
| categorisation with embeddings on and off | `tests/test_categorization.py::test_placement_in_both_modes[embeddings-on / embeddings-off]`, plus the cross-lingual, incremental re-embedding and slow-model fallback tests |
| README: running, service name + port, backup/restore, adding a provider, architecture | `README.md` |

Test totals at the final commit: **83 backend tests** (`scripts/test.sh`),
**11 browser tests** (`scripts/e2e.sh`, Playwright/Chromium), and
`scripts/smoke.sh --full` (deployment invariants plus SSE, WebSocket and a
512 MB streaming upload through the proxy). The backup/restore scripts were
tested with a full round trip on an isolated copy of the stack.

## How to run

```sh
docker network inspect webnet >/dev/null 2>&1 || docker network create webnet
docker compose up -d --build
docker compose logs backend | grep "setup code"
```

Tests: `scripts/test.sh` (backend), `scripts/e2e.sh` (browser, isolated stack),
`scripts/smoke.sh [--full]` (deployment). Details are in the README.

## Open work

Open work, ideas and plans are tracked as issues on the [Features board](https://github.com/users/enricocovili/projects/1)
(Status: Idea › Da definire › Pronta › In corso › Fatta; Area as the commit prefixes; Priorità P0–P2). A commit that
finishes an issue says `Closes #N`. What was open when the board was set up:

* **Real AI providers and models** ([#1](https://github.com/enricocovili/lecta/issues/1)): the Anthropic, OpenAI and
  Gemini adapters were never called against the real services (#2); quality, cost and latency of the import (#3), of the
  assistant (#4) and of lessons with strokes as pictures (#5) are unmeasured. The fake provider only checks the mechanics.
* Drawing on a real tablet and stylus (#6).
* Splitting one group across several chapters has no end-to-end test: the fake classifier always returns one placement (#7).
* Language detection and "photo vs. page of notes" are heuristics (#8).
* Idle memory is about 1 GB, ~720 MB of it the worker with the embedding model resident (#11); setting the embedding model
  to *off* in Settings saves it (retrieval then runs lexical-only).
* `docs/PLAN.md` describes the architecture before the redesigns (#13); the interface prototype PDF still shows the old
  name (#12).

## Limitations (by design)

* **LuaTeX's Lua `io` library isn't restricted by `openin_any`.** Mitigation: the
  compile container has no network and only mounts build directories, with no
  secrets or uploads (DECISIONS #19).
* **Rate limits are in memory** (per backend process; there is only one).
* Binary files in proposals (e.g. fallback images) can be included/excluded but not edited.
* The TeX Live image is large (~5.5 GB, full scheme as specified).

## Known issues

* The frontend build prints a harmless Astro warning about Shiki and CSP (Shiki isn't used): #10.
* PyMuPDF logs a deprecation warning for the `fitz` import name: #9.

## Where things are

* Design: `docs/PLAN.md` · decisions and alternatives: `docs/DECISIONS.md`
* AI gateway: `backend/app/egress/` · pipelines: `backend/app/pipeline/`
* Compile service: `latex/compile_server.py` · frontend proxy: `frontend/server.mjs`
