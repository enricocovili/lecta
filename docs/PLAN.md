# Lecta — implementation plan

> Written before implementation. The implementation follows it; deviations and
> refinements are recorded in `DECISIONS.md` and the final state in `STATUS.md`.

Self-hosted university-notes platform: a private LaTeX workspace for one admin
plus a public site that exposes only published PDFs. AI features (ingestion,
placement, diagram recreation, review chat) all go through one privacy gate.

## 1. Services (docker compose)

| service            | image / base                         | networks                 | role |
|--------------------|--------------------------------------|--------------------------|------|
| `lecta-frontend` | node 24 (bookworm-slim)              | `internal`, `webnet`     | Node server: Astro SSR (node adapter, middleware mode) + streaming `/api` proxy (HTTP, SSE, WebSocket, uploads) |
| `backend`          | python 3.12 slim (shared app image)  | `internal`, `egress`     | FastAPI API, auth, privacy gate, chat streaming |
| `worker`           | same image as backend                | `internal`, `egress`     | Postgres-backed job queue: ingestion, diagrams, placement, indexing, publishing |
| `latex`            | texlive/texlive TL2025 (full)        | **none** (`network_mode: none`) | Compile service, HTTP over a unix socket on a shared volume |
| `db`               | pgvector/pgvector (pg17)             | `internal`               | PostgreSQL + pgvector |

* `internal` is an `internal: true` bridge: no route to the internet.
* `egress` is an ordinary bridge used only by backend/worker to reach AI
  providers that I configure (the privacy gate still decides what's allowed).
* `webnet` is external and only `lecta-frontend` joins it. No `ports:` anywhere.
* The compile container has no network at all. It talks to backend/worker over
  `/run/lecta/latex.sock` on the `latex-sock` volume.

Volumes: `db-data` (Postgres), `app-data` (secrets, blobs, uploads, publications,
audit copies; backend + worker only), `latex-work` (per-project build dirs,
shared by backend/worker/latex), `latex-sock` (the socket).
The compile container can't see `app-data`, so even a sandbox escape can't reach
secrets or uploaded originals.

## 2. Backend layout (`backend/app`)

```
main.py            FastAPI app, middleware (security headers, CSRF, proxy headers)
config.py          optional env (DATABASE_URL, DATA_DIR, LATEX_SOCKET, ...) with defaults
db.py, models.py   SQLAlchemy 2 async (asyncpg), declarative models
secrets.py         first-boot generation of internal secrets in DATA_DIR/secrets
security/          argon2 passwords, sessions, CSRF, rate limiting, TOTP
api/               routers (public, setup, auth, courses, files, compile, revisions,
                   publish, uploads, jobs, approvals, audit, providers, prompts,
                   proposals, inbox, chat, search, settings, export)
services/          blobs (content-addressed store), projects (file tree + revisions),
                   templates, latex client, publish, search index, settings, prompts
egress/            THE ONLY code that talks to AI providers
  gate.py          privacy gate: ZDR check, approvals, provenance, audit, cost
  adapters/        anthropic, openai, gemini, openai_compat, fake
  transport.py     the only HTTP client used for providers
pipeline/          ingestion stages 1–4, diagrams, placement, retrieval, embeddings
worker/            queue runner (SKIP LOCKED + LISTEN/NOTIFY), job context, checkpoints
```

## 3. Data model (main tables)

* `admin_user` (single row): username, argon2 hash, totp secret (encrypted), totp_enabled.
* `sessions`: id (random), user, created/last_seen/expires, csrf token, ip, UA.
* `settings`: key → JSON (all app configuration lives here, edited in the UI).
* `prompts`: key, version, text, active flag (shipped defaults in code).
* `courses`: name, slug, academic_year, language, tags, preamble override,
  engine override, published flag, current publication. A special `inbox` concept
  is modelled by `inbox_items`, not a course.
* `chapters`: course, position, slug, title, file path (`chapters/NN-slug.tex`), publishable.
* `project_files`: (course, path) → blob hash, size, mime, text content + tsvector
  (for full-text search), origin (e.g. `source` for images cropped from uploads).
* `revisions`: course, parent, message, kind (manual/ai/restore/import),
  manifest JSON {path: blob hash}. Content-addressed ⇒ cheap snapshots.
* `publications`: course, created_at, pdf blob, chapter list with per-chapter PDFs.
* `uploads` / `source_files`: every uploaded file (original kept privately),
  sha256, detected type, original name/folder (untrusted hints), links to
  course/chapter it contributed to (`source_links`).
* `jobs`, `job_logs`, `job_steps` (memoised step results ⇒ resumable jobs).
* `ingest_items`: manifest entries (file/page, kind, text, preview, language, excluded).
* `providers`: type, name, base URL, encrypted API key, is_local, zdr + notes,
  models with pricing (JSON), enabled.
* `role_assignments` (in settings): role → {provider, model, fallback}.
* `approvals`: kind (request/plan/chat), status, payload JSON (exact payload),
  provenance, token/cost estimate, edits, job/chat links.
* `audit_log`: timestamp, provider, model, payload sha256, stored payload copy
  (pruned per retention), tokens in/out, cost, approval, job.
* `proposals` + `proposal_files`: structured edits, base hashes, new content,
  compile status/log, preview PDF, figure previews, status per file.
* `inbox_items`: uncategorised groups with generated LaTeX, sources, best guesses.
* `chat_sessions`, `chat_messages`, `session_grants` (session-scoped approvals).
* `index_chunks`: course/chapter/section, text, content hash, tsvector (per
  course language), `vector(384)` embedding (nullable).

## 4. Key flows

* **Auth**: setup wizard (requires a one-time setup code printed in backend logs),
  argon2id, httpOnly SameSite=Lax session cookie (Secure behind HTTPS),
  double-submit CSRF token, per-IP login rate limit, optional TOTP.
  Anonymous requests to anything private → 404.
* **Compile**: backend syncs the project's files into `latex-work/<course>/draft`
  (only changed files), then calls the compile service. The compile service keeps
  one running + one pending compile per project key (newer supersedes pending),
  runs interactive requests before background ones, precompiles `figures/*.tex`
  standalone with the course preamble (cached by hash), and runs latexmk with
  `openin_any=p openout_any=p`, no shell escape, rlimits, timeout, per-job TMP/HOME.
  Draft builds use `\includeonly` for the chapter being edited; publish builds are
  clean builds in a fresh dir with `\review` stripped.
* **Privacy gate**: every provider call is `gate.request(EgressRequest, ctx)`.
  Cloud + not ZDR ⇒ refused. Local ⇒ sent (audited). Cloud ⇒ allowed only by an
  approved plan (job), an active session grant (chat), or an explicit approval of
  this exact payload. Every content part carries provenance
  (`prompt`, `source:file/page`, `generated:<request>`, `chapter:<path@hash>`,
  `user`). A job follow-up is auto-allowed only if all parts are `prompt`,
  approved sources or generated inside the same job. Anything else ⇒ new, smaller approval.
* **Jobs**: resumable. Job code is a sequence of memoised steps; when the gate needs
  approval the job parks in `awaiting_approval` and is re-queued on decision.
  Responses are cached per (job, request key) so re-runs never re-send.
* **Ingestion**: stage 1 local (unpack, sniff, classify pages, preprocess photos,
  pandoc markdown) → plan approval → vision (classify unknown pages, transcribe
  handwriting, understand slides + figure boxes) → grouping/alignment → per-group
  generation (chunked, glossary/outline) → diagrams → compile + bounded auto-fix →
  placement (hybrid retrieval + classifier) → proposals / inbox items.
* **Proposals**: structured edits validated server-side, diffed, test-compiled in
  an isolated build key; UI shows per-hunk accept/reject (`@codemirror/merge`).
  Accept ⇒ revision + recompile.

## 5. Frontend (`frontend/`)

Astro 5 SSR (`@astrojs/node`, `mode: "middleware"`) mounted in `server.mjs`
(node core `http`), which also proxies `/api/*` to the backend with streaming both
ways and WebSocket upgrade piping. Pages are Astro routes with React islands:
editor (CodeMirror 6 + LaTeX mode), PDF viewer (pdfjs-dist, self-hosted worker),
merge/diff view, chat, uploads (XHR streaming, camera capture), approvals,
jobs, settings. No external CDNs/fonts. Telemetry disabled.

## 6. Milestones

1. Skeleton: compose + networks, Node server + proxy, FastAPI, Postgres/pgvector,
   job queue, setup wizard, auth, courses/chapters CRUD, folder view, homepages,
   public/private boundary tests.
2. LaTeX: compile service, incremental builds, figure cache, editor + PDF preview +
   error navigation, revisions, publishing.
3. Providers + privacy gate + approvals + audit + fake provider.
4. Ingestion stages 1–4, diagrams, review queue with diffs.
5. Categorisation: hybrid retrieval, embeddings + benchmark + lexical fallback,
   inbox, phone quick upload.
6. Review chat with diff proposals.
7. Polish: search, export/backup, mobile UX, docs, STATUS.

## 7. Testing

* `backend/tests` (pytest, run inside the backend container against a throw-away
  database and data dir, with the real compile service over the socket).
* Route sweep: every non-public route × every method without a session ⇒ 404.
* Egress isolation test: static scan (no provider SDK / HTTP client outside
  `app/egress`, adapters imported only by the gate) + runtime checks.
* End-to-end ingestion of synthetic fixtures (generated PDF with a diagram,
  handwriting-like image, Markdown with math + Mermaid, mixed zip) with the fake provider.
* Categorisation with embeddings on and off.
* `scripts/smoke.sh`: compose up, `docker inspect` checks (no published ports,
  only the frontend on webnet), HTTP checks through `lecta-frontend:4321` from a
  container on `webnet`, proxy SSE/WS/upload checks.
