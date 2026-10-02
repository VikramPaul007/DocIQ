# DocIQ

AI-assisted, configuration-driven document intelligence platform. See
[docs/product-brd.md](docs/product-brd.md) for the business requirements and
[docs/technical-implementation-plan.md](docs/technical-implementation-plan.md)
for the full architecture and build plan this repo follows.

**Status:** Milestones 1–2 (infra skeleton, auth/tenancy/audit) are in place and
verified: migrations, RLS, immutability triggers, the auth/workspace API, and
the login/workspaces frontend all pass their tests and work end-to-end over
HTTPS. Templates, rules, the processing pipeline, and review are under active
build — see the plan's "Build order" section for what's next. Until the worker
pipeline lands, `app/worker.py` doesn't exist yet, so the `worker` container
will restart-loop if started; that's expected, not a bug (`docker compose stop
worker` to quiet it in the meantime).

## Architecture

```
Browser ──HTTPS──> Caddy (TLS, security headers, serves SPA, /api → api)
                     │
                     ├─ api     (FastAPI)     ── Postgres 16 (data, jobs, sessions, audit)
                     └─ worker  (pipeline)     ── MinIO (encrypted documents)
```

Everything runs locally via Docker Compose — no cloud dependency required for
development. Two Postgres roles enforce least privilege: `dociq_app` (subject
to row-level security, used by the API) and `dociq_worker` (BYPASSRLS, used by
the background worker). `dociq_owner` is only used for migrations and the
`create-admin` CLI.

## First-time setup

1. Copy `.env.example` to `.env` and fill in real secrets (the app refuses to
   start on placeholder values):
   ```sh
   cp .env.example .env
   python -c "import secrets; print(secrets.token_hex(32))"   # SESSION_SECRET
   python -c "import secrets; print(secrets.token_hex(32))"   # DOCUMENT_ENCRYPTION_KEY
   ```
2. Build and start the stack:
   ```sh
   docker compose up -d --build
   ```
   This builds the images, runs `python -m app.cli migrate` (idempotent: bootstraps
   the `dociq_app`/`dociq_worker` DB roles, then `alembic upgrade head`), and
   starts the API, worker and frontend behind Caddy.
3. Create the first platform admin (interactive — password is never passed as
   an argument):
   ```sh
   docker compose exec -it api python -m app.cli create-admin
   ```
4. Open <https://localhost> (Caddy uses a self-signed cert for local dev —
   your browser will warn once; that's expected).

## Day-to-day

- Rebuild after a backend/worker code change (no live-reload by design — a
  source bind-mount would shadow the venv baked into the image):
  ```sh
  docker compose up -d --build api worker
  ```
- Frontend changes hot-reload automatically (Vite dev server, source bind-mounted).
- New backend dependency: add it to `backend/pyproject.toml`, then rebuild.
- New Alembic migration:
  ```sh
  docker compose exec api sh -c "uv sync && alembic revision -m 'add_x'"
  ```

## Tests

Dev dependencies aren't baked into the running containers (keeps the images
lean); install them on demand inside the already-migrated `api` container:

```sh
docker compose exec api sh -c "uv sync && pytest -q"
docker compose exec api sh -c "uv sync && ruff check ."
docker compose exec api sh -c "uv sync && mypy app"
```

Frontend:

```sh
cd frontend && npm ci && npm run typecheck && npm run lint
```

## Security notes

- Sessions: server-side, `__Host-` cookie, httponly/secure/SameSite=Strict,
  30 min idle / 12 h absolute timeout. CSRF token handed to the client via
  `/api/auth/me` and echoed back in `X-CSRF-Token` on every mutating request.
- Passwords: argon2id, 12–128 chars, no complexity theater (NIST 800-63B).
  5 failed attempts locks the account for 15 minutes; a separate per-IP
  counter throttles login attempts overall.
- Tenancy: every workspace-scoped table is denormalized with a direct
  `workspace_id` and protected by Postgres row-level security — app-level
  checks (`require_workspace_role`) are the primary gate, RLS is the backstop
  that holds even if an app-layer check is ever missed.
- Audit: `audit_events` and `reviews` are insert-only at the DB level (a
  trigger blocks UPDATE/DELETE regardless of which role is connected).
- Secrets: never committed. `.env` is gitignored; `.env.example` holds only
  placeholders the app explicitly refuses to boot with.
