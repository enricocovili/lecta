# Lecta

Self-hosted platform for university notes, worked on with an AI assistant.

* A **private workspace** for one admin: courses → chapters, uploads that turn
  into study notes by themselves, and an **AI assistant** that can read and change
  everything in a course. You read a **draft** typeset by the real LaTeX, block by
  block, pick blocks to ask about them, and the assistant's changes are applied at once (one click
  undoes a whole answer). There is no LaTeX editor: the LaTeX project is stored
  behind the scenes and can be downloaded (`.zip`) whenever you want.
* A **public site** that exposes only the courses you switched ON, always in
  their latest version (PDF, plus the LaTeX source to download).

Everything runs in Docker. All application settings live in the web UI; no
`.env` file is needed.

> Status: see [docs/STATUS.md](docs/STATUS.md). Design: [docs/PLAN.md](docs/PLAN.md).
> Choices made along the way: [docs/DECISIONS.md](docs/DECISIONS.md).

## Running

Requirements: Docker with Compose v2, and the external network `webnet`.

```sh
docker network create webnet   # only if it doesn't exist yet
docker compose up -d --build
```

The only service reachable from outside the stack is:

| service name       | internal port |
|--------------------|---------------|
| `lecta-frontend` | `4321` (HTTP) |

The container carries the same name (`container_name: lecta-frontend`), not `lecta-lecta-frontend-1`.

It is attached to `webnet`; point your HTTPS reverse proxy at
`http://lecta-frontend:4321`. It must forward `X-Forwarded-Proto` (and
`X-Forwarded-For`), which the app uses for secure cookies and rate limiting.
No service publishes a host port, and nothing else is attached to `webnet`.

### First run

1. Get the one-time setup code:
   `docker compose logs backend | grep "setup code"`
   (or `docker compose exec backend cat /data/secrets/setup-code`).
2. Open the site: you are redirected to `/setup`. Enter the code, then choose
   the admin username and password.
3. Everything else (providers, prompts, LaTeX template, limits, …) is set from
   **Settings** in the browser.

Internal secrets (DB password, encryption key for API keys, setup code) are
generated on first boot and stored in the `secrets` and `app-data` volumes.

## Services

| service            | what it does | networks |
|--------------------|--------------|----------|
| `lecta-frontend` | Node server: Astro SSR + React islands, streaming `/api` proxy (HTTP, SSE, WebSocket, large uploads) | internal, webnet |
| `backend`          | FastAPI: auth, API, chat streaming | internal, egress |
| `worker`           | Postgres-backed job queue (imports, placement, indexing, publishing) | internal, egress |
| `latex`            | TeX Live compile service over a unix socket | **none** |
| `db`               | PostgreSQL 17 + pgvector | internal |

`internal` has no internet access. `egress` is used only by backend and worker
to reach the AI providers you configure (all calls go through `backend/app/egress/gate.py`).

## Tests

```sh
scripts/test.sh            # backend test-suite (pytest), inside the app image
DEV=1 scripts/test.sh -k x # mount ./backend instead of the built image
scripts/smoke.sh           # deployment invariants + anonymous checks on the running stack
scripts/smoke.sh --full    # + isolated copy of the stack: setup, login, SSE, WebSocket, 512 MB upload
scripts/e2e.sh             # browser tests (Playwright/Chromium in Docker) on an isolated stack
```

`scripts/test.sh` uses a throw-away database on the stack's Postgres and the
real compile service.

## The workspace

Open a course: the centre is the **draft**, the left rail is the outline (chapters and sections,
sources, structure actions), the right panel is the **AI assistant**.

* **Draft vs. PDF**: the draft is typeset by the same LaTeX as the PDF, with the
  course's own preamble (theorem boxes, numbering, references, TikZ, tables, pictures
  look exactly as in the PDF), but **block by block**: every paragraph, heading and
  environment is its own SVG picture, cached. An edit typesets only the blocks that
  changed and the numbered ones it renumbers (with pdflatex well under a second; the
  first view of a course takes a couple of seconds per chapter). A block with a LaTeX
  error says where and shows its source; the others are still drawn. The PDF tab
  compiles the whole document on demand; publishing always uses the full build.
* **Downloads**: *Sorgente LaTeX* (`GET /api/courses/{id}/source.zip`: `main.tex`,
  `preamble.tex`, chapters, images, figures, a README on how to compile) and *PDF*
  (compiled now). The public reader offers the PDF and the LaTeX source of the
  published version (`/api/public/courses/{slug}/source.zip`, built with every publish).
* **Publishing is ON/OFF** (top bar of the course, or the switch on the Home).
  While ON, the public site shows the latest version: every change (imports,
  assistant) is rebuilt automatically about 2 minutes after the course stops
  changing (`LECTA_REPUBLISH_QUIET_S`). The public build is a clean full build
  without `\review` markers, split per chapter for the reader. If a rebuild
  fails, the previous PDF stays online and the course shows the error.
* There is no revision history: edits and imports write straight into the files;
  the only safety net is **Annulla** on the assistant's last answers.

### How a course is stored

Each course is one LaTeX project: `main.tex` (layout + a managed `\include`
block), `preamble.tex` (generated from the global template or the course's
override), `chapters/NN-slug.tex`, `images/` and optionally `figures/*.tex`
(TikZ pictures, just the picture code). Images are placed with
`\lectaimage[caption]{images/name.png}`; `\lectafigure[caption]{name}` includes
`figures/name.tex`; `\review{...}` is a highlighted note that disappears in
published builds. Chapters and positions are managed by the assistant and the
outline (adding, renaming, moving and deleting keep `main.tex` in step).

### LaTeX sandbox

The compile container has no network, runs as an unprivileged user with a
read-only root filesystem and all capabilities dropped, and mounts only the
build volume (no secrets, no uploads). Each run has shell escape disabled,
`openin_any=p` / `openout_any=p` (no absolute paths, no `..`, no dot files),
latexmk with `-norc` and a fixed rc file (a project can't ship a `latexmkrc`),
a fresh temporary HOME/TEXMFVAR, rlimits (CPU, memory, file size) and a
wall-clock timeout that kills the process group. `tests/test_latex.py` checks the escapes.

## Lessons: taking notes in class

**Lezioni** (`/admin/lessons`) is for the lecture itself. Create a lesson in a subject, load the slides as a PDF (or none:
you get blank pages) and write while the lecturer talks: **Markdown notes next to every slide** and **pen strokes over it**
(pen, highlighter, eraser, undo of everything: strokes, text and removed slides), with mouse, stylus or finger, on a tablet or a foldable laptop. A finger scrolls unless you
turn on *Dito scrive*; a resting palm is ignored while a stylus is around. Add blank pages where a slide has no room left,
switch between slide + notes side by side, notes below, or slides only. Everything is saved once a minute and on Ctrl+S (it keeps trying
when the connection drops) and the screen stays on.

**Integra appunti** turns the lesson into the study text of the subject: your notes are the backbone (a section per slide),
the slides complete them, and what you drew or wrote on a slide is read from a picture of the annotated slide. A lesson is **In corso** until its text is merged into the
subject's notes, then **Completata** (click the state to switch it by hand). Before it
starts you are asked for the subject's **writing guidelines** (kept for next time, editable in the subject's settings); with
none, a notice says the text will be generated automatically. Downloads: the slides with your strokes (PDF) and the notes (`.md`).
**Condividi** gives links that open the lesson without signing in: read-only (slides, notes and strokes, following the lecture as you
write) or editable (the same editor, minus what is yours alone). Revoke or replace them whenever you want.
Details: [docs/LESSONS.md](docs/LESSONS.md).

## Uploading material (ingestion)

**Upload** (or the phone page `/admin/quick`) accepts zips with nested folders
and loose files, freely mixed: **the class notes** (`.md`/`.txt`, the bullet
points taken during the lecture), PDFs (slides, annotated slides, scans, typed
pages), photos of handwritten notes (JPEG/PNG/HEIC). Choose a course and/or
chapter, or leave both empty to let Lecta file the material. Files are
streamed to disk (hundreds of MB are fine) and detected by magic bytes. File
and folder names are only hints.

The class notes are the backbone of the text. When an upload has no `.md`/`.txt`
file (zips are looked into), **Carica ed elabora** warns first: add the notes to
the same upload, or go on and get a summary of the slides alone
(`POST /api/uploads/{id}/finish` answers 409 `no_notes` unless `without_notes`
is set). Quick uploads from the phone don't ask: their photos are the notes.

Clicking **Carica ed elabora** starts a job that runs to the end by itself
(Estrazione › Lettura › Stesura › Inserimento):

1. **Extraction** (local, no AI): safe unpacking (zip-slip / zip-bomb protection,
   junk skipped, unsupported files reported). PDFs: the text layer in reading
   order with sub/superscripts rebuilt (`x^{2}`, `x_{i}`), headings and slide
   titles; repeated headers/footers, logos and theme graphics dropped; embedded
   images and vector drawings (plots, diagrams, found by clustering the drawing
   commands) become images with a `[[IMG id]]` marker where they sit; beamer
   overlay steps and blank pages are skipped. A page is read *with its picture*
   only when text isn't enough: scans, garbled text layers, handwritten ink,
   display math, formulas stored as images. Photos are EXIF-rotated, cropped to the
   page, deskewed and contrast-enhanced. Notes are kept as written, with their
   images marked.
2. **Grouping**: one chapter per folder (pictures in `img/`, `figures/`, … go with
   their folder). At the root: with one notes file, everything together; with
   several, each notes file with the PDFs whose names match it ("Lezione 4.pdf"
   with "appunti lezione 4.md"); without notes, one chapter per PDF and one for
   all loose photos.
3. **Reading**: the material in units of ~10 pages of one file (Settings → AI e
   costi), all in parallel, converted faithfully to LaTeX: math as LaTeX, every
   picture placed with `\lectaimage`, nothing left out (prompt `read.pages`).
   A reply cut off at the output limit splits the unit in two.
4. **Writing** (prompt `notes.compose`): the study text of each chapter, in prose.
   It follows the order and points of the class notes and completes them with
   the material (definitions, formulas, derivations, examples, the useful
   plots and diagrams), compact but without losing concepts; without notes the
   material is summarised the same way. It is written in the course's language
   (or the notes'). A long text is written in parts in parallel (slices of the
   notes, each with the relevant material), and a reply cut off splits its part.
5. **Placement** (see below) → **compile check** in a scratch project with at most
   one automatic AI fix (Settings → LaTeX) → **written into the course**: a new
   chapter, or appended to the chosen/matching chapter. Only material Lecta
   can't place confidently goes to **Da smistare**.

Every step is memoised: a retried job never repeats finished work or re-sends
anything, and writing into a course happens exactly once.

### Where material goes (categorisation)

Candidates come from **hybrid retrieval** over chapter content:

* lexical: PostgreSQL full-text search with each course's text-search
  configuration (italian / english / … / simple);
* semantic: local embeddings with
  `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (quantised ONNX
  via fastembed, CPU only, 384-d vectors in pgvector). Chapters are split into
  ~100-token windows (LaTeX stripped, each prefixed with its chapter/section
  heading), and only chunks whose content hash changed are re-embedded. Indexing
  runs in the worker at low priority with ONNX threads capped (default 2). The
  model is downloaded when the image is built and stays resident in the worker.

On first boot the worker runs a short **benchmark** (shown in Settings →
Embeddings). If embeddings are off, unavailable or slower than the configured
threshold, retrieval **falls back to lexical-only** automatically.

The classification model gets the notes plus the top-k candidates' titles and
section outlines, and returns the best placement with confidence and rationale:
append to an existing chapter or a new chapter in a course. Below the threshold
(and without a course chosen at upload) the notes go to **Da smistare**, where one
click assigns them to a course/chapter or creates a new course (language detected
from the material); they are written right away.

### Phone quick upload

`/admin/quick` is a phone-friendly page with camera capture (several photos at
once) that feeds the same pipeline. Add it to the home screen: the web manifest
opens it directly.

## The AI assistant

The panel on the right of a course is an **agent with tools** (contract:
[docs/AI-WORKSPACE.md](docs/AI-WORKSPACE.md)). Per turn it decides what to look at and
what to do:

* **Reads everything in the course**: `course_overview`, `read_file` (any chapter,
  `main.tex`, `preamble.tex`, figures), `grep`, `find_related` (hybrid retrieval),
  the uploaded sources (`list_sources`, `read_source`, `view_source_page`: it can
  *look* at a slide or a photo) and images (`view_image`).
* **Changes everything**: `edit_file` (exact search/replace), `write_file`,
  `create_chapter`, `rename_chapter`, `move_chapter`, `delete_chapter`, `delete_file`,
  `rename_file`, the preamble and `main.tex` (its chapter list stays managed); and
  verifies with `check_build` (real compile, errors with file and line).
* **Applied immediately.** The first write of a turn snapshots the course; the change
  card under the answer lists the files (+/− lines) and has **Annulla**, which restores
  the whole turn (refused if those files changed again since: undo the newer ones first).
* **Pick to ask**: click a block of the draft (Shift+click extends to a range of blocks)
  and a small menu offers three buttons.
  *Rimuovi* takes that passage out at once (the assistant also makes small formatting fixes
  around it, nothing else; mode `edit`). *Spiega* and *Correggi* open the chat with the
  passage as context and wait for what you want to know (mode `explain`, the answer never
  touches the document) or what to correct (mode `edit`). The pick travels with its
  source line range and LaTeX.
* **Document feedback**: an earlier review (score, strengths, issues with a *Correggi* button)
  still shows in the chat and on the home page; the top bar no longer has a *Revisione AI* button.
* Turns run in the background (closing the tab doesn't stop them; reopen and the
  panel reattaches), one per course at a time, up to `agent_max_steps` model rounds
  (Settings → AI e costi, default 40). Uploaded material and course text reach the model
  fenced as untrusted data.

The assistant needs a model with tool support (Anthropic, OpenAI-compatible chat
completions, Gemini): Settings → Models → *Assistente AI*.

## Search, export and backups

* **Search** (`/admin/search`): full-text over all course text (per-course
  text-search configuration, highlighted snippets), plus
  course/chapter titles and uploaded file names. The public `/search` covers
  published course and chapter titles only.
* **Export all** (Settings → Backup & export): a streamed zip of every course's
  sources (including the preamble in use), the latest draft PDF and the
  published PDF, optionally with the uploaded originals.

### Backup and restore

```sh
scripts/backup.sh                         # → backups/lecta-YYYYmmdd-HHMM.tar.gz
scripts/restore.sh backups/lecta-….tar.gz
```

The backup contains a `pg_dump` of the database and the `app-data` (blobs,
uploads, publications, audit copies) and `secrets` volumes. `secrets` holds the
key that decrypts your provider API keys, so keep the archive safe. Build
directories (`latex-work`) and the socket volume are not needed; they're
recreated automatically. `restore.sh` stops the stack, restores the volumes and
the database (resetting the DB password to the restored secret), and starts
everything again. Set `PROJECT=<compose project>` if you don't use the default
`lecta`. The same thing by hand:

```sh
docker compose exec -T db pg_dump -U lecta -d lecta -Fc > db.dump
docker run --rm -v lecta_app-data:/v:ro -v "$PWD":/out alpine tar -C /v -czf /out/app-data.tar.gz .
docker run --rm -v lecta_secrets:/v:ro -v "$PWD":/out alpine tar -C /v -czf /out/secrets.tar.gz .
```

### Locked out?

```sh
docker compose exec backend python -m app.cli reset-password   # prompts; signs out all sessions
docker compose exec backend python -m app.cli disable-2fa
docker compose exec backend python -m app.cli logout-all
```

## AI providers

### Adding a provider

Settings → **Providers** → *Add provider*:

1. Choose the type: Anthropic, OpenAI, Google Gemini, OpenAI-compatible
   (OpenRouter, Mistral, vLLM, Ollama, LM Studio, …) or Fake (tests/dev).
2. Base URL (optional; defaults to the official endpoint) and API key (stored
   encrypted with a key generated on first boot, never shown again).
3. *Test connection* / *Fetch models*, then set per-model prices (USD per
   million tokens) for the cost log.
4. Settings → **Models**: assign a model (and an optional fallback, used when the
   primary fails) to each role: pages read with their picture (vision),
   handwriting, text pages and fixes, placement, AI assistant (needs tool calling).

The material you upload and your chat messages are sent to the providers you
configure; choosing providers you trust (and their data-retention terms) is up to
you. Settings → **AI e costi** shows the request limits, costs per month and per
job, and a log of every call (provider, model, task, tokens, cost, duration,
errors; payloads are not stored).

To add a new provider *type* in code: implement `Adapter` in
`backend/app/egress/adapters/<name>.py` (complete, stream, list_models), use
`transport.request_json/stream_sse`, and add it to `ADAPTERS` in
`adapters/__init__.py`.

Uploaded content (including file and folder names) is wrapped in per-request
random fences and prompts instruct models to treat it as data; model LaTeX is
sanitised (no `\input`, `\write18`, preamble commands…) and compiled in the sandbox.

## Configuration

All application settings are in the web UI (Settings). The compose file needs
no variables. The optional environment variables (defaults in parentheses)
exist only for unusual setups:

| variable | used by | meaning |
|----------|---------|---------|
| `LECTA_COOKIE_SECURE` (`auto`) | backend | `auto` = Secure cookie when `X-Forwarded-Proto: https`; `always` / `never` |
| `LECTA_DATABASE_URL` | backend, worker | override the DB URL (default: `db` host + generated password) |
| `LECTA_WORKER_CONCURRENCY` (`4`) | worker | jobs running at once (CPU-bound work is capped at 2 regardless) |
| `LECTA_LATEX_CONCURRENCY` (`2`), `LECTA_LATEX_MEM_MB` (`2048`) | latex | parallel compiles, memory limit per TeX run |
| `LECTA_BACKEND_URL` (`http://backend:8000`) | frontend | where the proxy sends `/api` |
| `LECTA_LOG_LEVEL` (`INFO`) | backend, worker | log level |
| `LECTA_REPUBLISH_QUIET_S` (`120`) | worker | a published course is rebuilt after this many seconds without changes |

## Architecture

See [docs/PLAN.md](docs/PLAN.md) for the full design and
[docs/DECISIONS.md](docs/DECISIONS.md) for the reasoning. In short:

```
   HTTPS reverse proxy (on webnet)
                 │
                 ▼  http://lecta-frontend:4321
       ┌──────────────────┐
       │ lecta-frontend │  Astro SSR + /api proxy        networks: internal, webnet
       └────────┬─────────┘
                │ /api (internal network, no internet)
                ▼
       ┌──────────────────┐        ┌──────────────────┐
       │     backend      │───────►│        db        │  Postgres + pgvector
       │     FastAPI      │   ┌───►│                  │  network: internal
       └───┬─────────┬────┘   │    └──────────────────┘
           │         │        │
  egress ◄─┘         │   ┌────┴─────────────┐
  (AI providers)     │   │      worker      │─► egress (AI providers)
                     │   │ jobs, embeddings │
                     │   └────────┬─────────┘
                     │ unix socket│ (latex-sock volume)
                     ▼            ▼
                ┌──────────────────────┐
                │        latex         │  TeX Live, network_mode: none
                └──────────────────────┘
```

* **Public/private boundary**: an ASGI guard in front of all routes returns 404
  to anonymous callers for every `/api` path that isn't explicitly public
  (`/api/public/*`, health, setup and auth endpoints), before routing. Every
  private route also depends on `require_admin`. Private admin pages also 404.
  `tests/test_boundary.py` sweeps every route × method anonymously.
* **Sessions**: argon2id password, httpOnly SameSite=Lax cookie (Secure over
  HTTPS), CSRF token required for unsafe methods, login rate limiting, optional TOTP.
  **Passkeys** (WebAuthn, Settings → Account): add one with the fingerprint, a PIN or a password manager such as Bitwarden and the
  sign-in page offers «Accedi con una passkey» — no username, password or 2FA code (a passkey with user verification is both factors);
  the password stays as the fallback. They need HTTPS (or localhost); the site's host is the relying party. `docs/DECISIONS.md` has the details.
* **Jobs**: `jobs` table, claimed with `FOR UPDATE SKIP LOCKED`, woken with
  LISTEN/NOTIFY. Jobs survive restarts, report progress and logs, and can be
  cancelled or retried. Steps are memoised, so a retry resumes where the job stopped.

* **Weak-CPU design** (i5-6500T): no local LLM/vision/OCR models; CPU-bound work
  in the worker is capped at 2 concurrent tasks and the worker runs niced; the
  compile service runs editor compiles before background ones and niced
  background builds, with at most 2 compiles at once; PDF extraction works on the
  text layer and drawing commands (a 55-slide deck takes ~2 s) and renders only the
  pages that go to a vision model; the embedding model is small, optional and
  capped at 2 ONNX threads.
* **Privacy hygiene**: telemetry disabled (Astro, Hugging Face); no external
  CDNs or web fonts (system font stack, pdf.js worker self-hosted);
  `e2e/tests/06-mobile-privacy.spec.ts` fails if a page loads anything from
  another origin.

### Repository layout

```
docker-compose.yml     the stack (no ports, webnet external)
backend/               FastAPI app + worker (same image)
  app/api/             routers            app/egress/   AI gateway + provider adapters (tool calling)
  app/pipeline/        import (pdfextract, read, apply), placement, publishing, the assistant (agent, agent_tools), indexing
  app/services/        projects, compile client, blocks + draft (typeset draft), source_export, retrieval, embeddings, settings…
  app/security/        auth, CSRF, ASGI guard       alembic/   migrations
  tests/               pytest suite (+ synthetic fixtures)
latex/                 compile service (TeX Live, stdlib Python, unix socket)
frontend/              Astro + React islands, server.mjs (proxy)
db/                    Postgres + pgvector image (password generated on first boot)
e2e/                   Playwright browser tests
scripts/               test.sh, smoke.sh, e2e.sh, backup.sh, restore.sh
docs/                  PLAN.md, DECISIONS.md, STATUS.md, AI-WORKSPACE.md, LESSONS.md
```
