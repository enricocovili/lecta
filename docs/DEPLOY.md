# Deploying and running Lecta

## Running

Requirements: Docker with Compose v2, and the external network `webnet`.

```sh
docker network create webnet   # only if it doesn't exist yet
docker compose up -d --build
```

The only service reachable from outside the stack is `lecta-frontend`, on internal port `4321` (HTTP). The container
carries the same name (`container_name: lecta-frontend`), not `lecta-lecta-frontend-1`.

It is attached to `webnet`; point your HTTPS reverse proxy at `http://lecta-frontend:4321`. It must forward
`X-Forwarded-Proto` (and `X-Forwarded-For`), which the app uses for secure cookies and rate limiting. No service
publishes a host port, and nothing else is attached to `webnet`. The services and networks are described in
[ARCHITECTURE.md](ARCHITECTURE.md).

## First run

1. Get the one-time setup code:
   `docker compose logs backend | grep "setup code"`
   (or `docker compose exec backend cat /data/secrets/setup-code`).
2. Open the site: you are redirected to `/setup`. Enter the code, then choose the admin username and password.
3. Everything else (providers, prompts, LaTeX template, limits, …) is set from **Settings** in the browser.

Internal secrets (DB password, encryption key for API keys, setup code) are generated on first boot and stored in the
`secrets` and `app-data` volumes.

## AI providers

Settings → **Providers** → *Add provider*:

1. Choose the type: Anthropic, OpenAI, Google Gemini, OpenAI-compatible (OpenRouter, Mistral, vLLM, Ollama, LM Studio,
   …) or Fake (tests/dev).
2. Base URL (optional; defaults to the official endpoint) and API key (stored encrypted with a key generated on first
   boot, never shown again).
3. *Test connection* / *Fetch models*, then set per-model prices (USD per million tokens) for the cost log.
4. Settings → **Models**: assign a model (and an optional fallback, used when the primary fails) to each role: pages
   read with their picture (vision), handwriting, text pages and fixes, placement, AI assistant (needs tool calling:
   Anthropic, OpenAI-compatible chat completions, Gemini).

The material you upload and your chat messages are sent to the providers you configure; choosing providers you trust
(and their data-retention terms) is up to you. Settings → **AI e costi** shows the request limits, costs per month and
per job, and a log of every call (provider, model, task, tokens, cost, duration, errors; payloads are not stored).

## Configuration

All application settings are in the web UI (Settings). The compose file needs no variables. The optional environment
variables (defaults in parentheses) exist only for unusual setups:

| variable | used by | meaning |
|----------|---------|---------|
| `LECTA_COOKIE_SECURE` (`auto`) | backend | `auto` = Secure cookie when `X-Forwarded-Proto: https`; `always` / `never` |
| `LECTA_DATABASE_URL` | backend, worker | override the DB URL (default: `db` host + generated password) |
| `LECTA_WORKER_CONCURRENCY` (`4`) | worker | jobs running at once (CPU-bound work is capped at 2 regardless) |
| `LECTA_LATEX_CONCURRENCY` (`2`), `LECTA_LATEX_MEM_MB` (`2048`) | latex | parallel compiles, memory limit per TeX run |
| `LECTA_BACKEND_URL` (`http://backend:8000`) | frontend | where the proxy sends `/api` |
| `LECTA_LOG_LEVEL` (`INFO`) | backend, worker | log level |
| `LECTA_REPUBLISH_QUIET_S` (`120`) | worker | a published course is rebuilt after this many seconds without changes |

## Backup and restore

```sh
scripts/backup.sh                         # → backups/lecta-YYYYmmdd-HHMM.tar.gz
scripts/restore.sh backups/lecta-….tar.gz
```

The backup contains a `pg_dump` of the database and the `app-data` (blobs, uploads, publications, audit copies) and
`secrets` volumes. `secrets` holds the key that decrypts your provider API keys, so keep the archive safe. Build
directories (`latex-work`) and the socket volume are not needed; they're recreated automatically. `restore.sh` stops the
stack, restores the volumes and the database (resetting the DB password to the restored secret), and starts everything
again. Set `PROJECT=<compose project>` if you don't use the default `lecta`. The same thing by hand:

```sh
docker compose exec -T db pg_dump -U lecta -d lecta -Fc > db.dump
docker run --rm -v lecta_app-data:/v:ro -v "$PWD":/out alpine tar -C /v -czf /out/app-data.tar.gz .
docker run --rm -v lecta_secrets:/v:ro -v "$PWD":/out alpine tar -C /v -czf /out/secrets.tar.gz .
```

## Locked out?

```sh
docker compose exec backend python -m app.cli reset-password   # prompts; signs out all sessions
docker compose exec backend python -m app.cli disable-2fa
docker compose exec backend python -m app.cli logout-all
```
