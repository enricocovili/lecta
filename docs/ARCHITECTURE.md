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
  path that isn't explicitly public (`/api/public/*`, health, setup and auth endpoints), before routing. Every private
  route also depends on `require_admin`. Private admin pages also 404. `tests/test_boundary.py` sweeps every route ×
  method anonymously.
* **Sessions**: argon2id password, httpOnly SameSite=Lax cookie (Secure over HTTPS), CSRF token required for unsafe
  methods, login rate limiting, optional TOTP. **Passkeys** (WebAuthn, Settings → Account): add one with the
  fingerprint, a PIN or a password manager such as Bitwarden and the sign-in page offers «Accedi con una passkey» — no
  username, password or 2FA code (a passkey with user verification is both factors); the password stays as the
  fallback. They need HTTPS (or localhost); the site's host is the relying party. `DECISIONS.md` has the details.
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
