# Architecture

The current shape of the system. [PLAN.md](PLAN.md) is the original design and [DECISIONS.md](DECISIONS.md) the
reasoning behind every change since.

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

## Services

| service            | what it does | networks |
|--------------------|--------------|----------|
| `lecta-frontend` | Node server: Astro SSR + React islands, streaming `/api` proxy (HTTP, SSE, WebSocket, large uploads) | internal, webnet |
| `backend`          | FastAPI: auth, API, chat streaming | internal, egress |
| `worker`           | Postgres-backed job queue (imports, placement, indexing, publishing) | internal, egress |
| `latex`            | TeX Live compile service over a unix socket | **none** |
| `db`               | PostgreSQL 17 + pgvector | internal |

`internal` has no internet access. `egress` is used only by backend and worker to reach the AI providers you configure
(all calls go through `backend/app/egress/gate.py`).

## Boundaries

* **Public/private boundary**: an ASGI guard in front of all routes returns 404 to anonymous callers for every `/api`
  path that isn't explicitly public (`/api/public/*`, `/api/invite/*`, health, setup and auth endpoints), before routing. Every private
  route also depends on an access level: `require_user` (any signed-in user), `require_content` (everyone reads,
  editors write), `require_editor` or `require_admin`; a signed-in user below it gets 403. Private admin pages also
  404 to anonymous visitors, and the pages only admins may use (the AI costs page) to everyone but admins
  (`frontend/src/middleware.ts`); menus and the settings sections hide what the role can't use, but that is only
  courtesy, the API checks every call. `tests/test_boundary.py` sweeps every route × method anonymously; `tests/test_access.py` lists every route's
  level and fails on a route it doesn't list.
* **Ownership**: every course has an owner (`security/access.py`). Editors read their own courses and the published
  ones and change only their own; viewers read every course; admins read and change everything. A course the user
  can't read is 404, one they can't change 403. Lessons, labs and import material are private to the owner (and admins,
  viewers) even when the course is published; a job belongs to whoever started it and to its course's owner;
  a conversation with the assistant to its author (admins see all), and asking it needs the right to change the course. Search,
  the home page and the assistant's retrieval only look in the courses the user may read. `tests/test_ownership.py` calls every route under a course as another
  editor. The course JSON (`/api/courses`, `/api/tree`, the home page's courses) carries `owner` and `can_write`: the
  UI shows others' courses as «di <owner>» and, when `can_write` is false, the course page and its text read-only (no
  assistant, writing in place, structure, settings, compile or new lessons); publishing controls are only for admins.
  Lessons (`/api/lessons`, `/api/lessons/{id}`) carry `can_write` too: a viewer opens them read-only, like a share
  link's read mode (no tools, notes, pages, assistant, «Integra appunti», nor any unsafe request), and share links are
  offered only to admins.
  Labs (`/api/lessons/{id}/lab` and its address by number) carry `can_write` as well: a viewer reads the files, their
  comments and the notes, with no upload, rename, delete, editing, comments, assistant or lab to create, and sends
  nothing but reads.
  **Draft notes** (`api/notes.py`, `draft_notes`) are the one thing a reader writes on someone else's course: any
  signed-in user but the owner comments on or suggests a change to a block of a *published* course; the author sees
  and changes their notes while open, the owner and admins see them all and accept (the block write of the draft),
  reject or resolve them. Notes are found again by their block's text after edits, or flagged «testo cambiato».
  In the read-only draft a click on a block offers «Commenta» / «Suggerisci modifica», and a marker on a block opens
  the reader's own notes on it (`workspace/DraftNotes.tsx`).
* **Sessions**: accounts live in `users`, each with a role (`viewer`, `editor`, `admin`) and possibly disabled (a
  disabled user's sessions stop working); argon2id password (optional: invited users have only passkeys), httpOnly SameSite=Lax cookie (Secure over HTTPS), CSRF token required for unsafe
  methods, login rate limiting, optional TOTP. **Passkeys** (WebAuthn, Settings → Account): add one with the
  fingerprint, a PIN or a password manager such as Bitwarden and the sign-in page offers «Accedi con una passkey» — no
  username, password or 2FA code (a passkey with user verification is both factors); the password stays as the
  fallback. They need HTTPS (or localhost); the site's host is the relying party. **Invites**: there are no sign-ups;
  an admin creates a single-use link with a role (`/api/invites`, shown once, seven days), and whoever opens it
  (`/invito/<token>`, public; a used or expired link is the generic 404) picks a username and creates a passkey: the
  account has no password and can't remove its last passkey. A **recovery link** (made from «Utenti» for a user who
  lost their passkey) is the same kind of link bound to that user: it adds a passkey to them and signs out their other
  sessions. **Users** (Settings → Utenti, `/api/users`, admins):
  change role, disable (drops the sessions), sign out everywhere, delete only without courses; the last active admin
  can't be demoted, disabled or deleted (checked under an advisory lock). `DECISIONS.md` has the details.
* **Model input and output**: uploaded content (including file and folder names) is wrapped in per-request random
  fences and prompts instruct models to treat it as data; model LaTeX is sanitised (no `\input`, `\write18`, preamble
  commands…) and compiled in the sandbox.
* **Privacy hygiene**: telemetry disabled (Astro, Hugging Face); no external CDNs or web fonts (system font stack,
  pdf.js worker self-hosted); `e2e/tests/06-mobile-privacy.spec.ts` fails if a page loads anything from another origin.

## LaTeX sandbox

The compile container has no network, runs as an unprivileged user with a read-only root filesystem and all
capabilities dropped, and mounts only the build volume (no secrets, no uploads). Each run has shell escape disabled,
`openin_any=p` / `openout_any=p` (no absolute paths, no `..`, no dot files), latexmk with `-norc` and a fixed rc file (a
project can't ship a `latexmkrc`), a fresh temporary HOME/TEXMFVAR, rlimits (CPU, memory, file size) and a wall-clock
timeout that kills the process group. `tests/test_latex.py` checks the escapes.

## Jobs

`jobs` table, claimed with `FOR UPDATE SKIP LOCKED`, woken with LISTEN/NOTIFY. Jobs survive restarts, report progress
and logs, and can be cancelled or retried. Steps are memoised, so a retry resumes where the job stopped.

## Weak-CPU design (i5-6500T)

No local LLM/vision/OCR models; CPU-bound work in the worker is capped at 2 concurrent tasks and the worker runs niced;
the compile service runs editor compiles before background ones and niced background builds, with at most 2 compiles
at once; PDF extraction works on the text layer and drawing commands (a 55-slide deck takes ~2 s) and renders only the
pages that go to a vision model; the embedding model is small, optional and capped at 2 ONNX threads.
