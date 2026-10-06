# Development

## Tests

```sh
scripts/test.sh            # backend test-suite (pytest), inside the app image
DEV=1 scripts/test.sh -k x # mount ./backend instead of the built image
scripts/smoke.sh           # deployment invariants + anonymous checks on the running stack
scripts/smoke.sh --full    # + isolated copy of the stack: setup, login, SSE, WebSocket, 512 MB upload
scripts/e2e.sh             # browser tests (Playwright/Chromium in Docker) on an isolated stack
```

`scripts/test.sh` uses a throw-away database on the stack's Postgres and the real compile service.

## Repository layout

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
scripts/               test.sh, smoke.sh, e2e.sh, backup.sh, restore.sh, board.sh (the planning board)
.github/               issue forms (bug, enhancement, idea), workflow that places new issues on the board
docs/                  this documentation
```

## Adding a provider type

Implement `Adapter` in `backend/app/egress/adapters/<name>.py` (complete, stream, list_models), use
`transport.request_json/stream_sse`, and add it to `ADAPTERS` in `adapters/__init__.py`. Every provider call goes
through `backend/app/egress/gate.py`.

## Planning: issues and the board

Open work lives on the [Features board](https://github.com/users/enricocovili/projects/1). An issue is one of three
kinds, each with its form and label: **bug** (something broken) and **enhancement** (a change already decided) start in
✅ Pronta, **idea** (to explore) starts in 💡 Idea; `.github/workflows/board.yml` places them (it needs the
`PROJECT_TOKEN` secret, a classic token with the `project` scope). A commit that finishes an issue says `Closes #N`.
`scripts/board.sh` does the same from the terminal (`new`, `status`, `area`, `prio`, `sub`, `list`).
