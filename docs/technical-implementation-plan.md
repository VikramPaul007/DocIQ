# DocIQ — Secure Implementation Plan (Backend + Frontend)

## Context

The repo contains only `docs/product-brd.md`, `docs/implementation-plan.md` and `CLAUDE.md`. There is no code yet. The goal is a working build of the whole BRD MVP (FR-01..FR-20), built with production-grade engineering. It is an academic project: sample data comes from the user, it runs locally in Docker Compose, and the user wants **the entire scope built in one pass**, so the 12-week phased schedule is replaced by ordered milestones that each have a verification gate.

### Confirmed decisions (from clarification)
| Area | Decision |
|---|---|
| Context | Academic project, production practices. Real-world security posture, synthetic/provided data |
| Deploy | Local laptop, Docker Compose only |
| LLM | Provider-agnostic `LlmProvider` interface; default adapter = **OpenAI-compatible** (base_url + key from env: works with OpenAI/Azure/vLLM/Ollama) |
| OCR | Two-step: **OCR adapter (PaddleOCR default)** → LLM maps text to template schema |
| Auth | Local accounts, argon2id, server-side sessions, fixed roles |
| Tenancy | Workspace-scoped membership with per-workspace role; app-level checks + **Postgres RLS backstop** |
| Queue | Postgres-backed jobs table (`FOR UPDATE SKIP LOCKED`) |
| Storage | MinIO (S3 API) + app-level AES-256-GCM encryption |
| Formats | PDF only (text-layer and scanned) |
| Frontend | React + Vite + TypeScript + shadcn/ui + Tailwind, TanStack Query, React Hook Form + Zod |
| Rules | JSON AST + own typed evaluator (no `eval`, no JSONLogic) |
| Test data | User provides samples; eval suite (P/R/F1) **deferred** |

### Decisions I made (flag any that are wrong)
1. **All processed records go to REVIEW_REQUIRED**, and the queue is sorted by risk (desc). Nothing is auto-approved, which matches "no automatic decision".
2. **Request correction** records the decision and closes the record. A corrected doc is re-uploaded as a new document linked via `supersedes_document_id`. Reviewers do not edit extracted values (not in the BRD).
3. **Roles**: `platform_admin` (global flag: user and workspace creation) plus a per-workspace role of `admin` (configure, publish, upload, review) or `reviewer` (queue + review only).
4. **Extraction validation failure does not stop the pipeline.** The record is flagged `extraction_valid=false`, rules still run (completeness rules catch the gaps), and the record goes to review with the flag visible.
5. **Summary stage is non-blocking.** After retries are exhausted it is marked failed and the pipeline continues.
6. **Date rules use the record's persisted `evaluated_at`** as "today", so replays are deterministic.
7. **Score = sum of weights of failed rules** (Decimal), compared to thresholds `low < medium < high` per version, with contributors persisted.
8. **Test-harness runs** reuse the pipeline as jobs with `kind='test'`. They snapshot the draft config into the job and never appear in the reviewer queue.
9. PDF parsing uses **pypdfium2** (Apache/BSD) rather than PyMuPDF, which is AGPL.

---

## Architecture

```
Browser ──HTTPS──> Caddy (TLS, security headers, serves SPA, /api → api)   [same-origin: no CORS]
                     │
                     ├─ api     (FastAPI, uvicorn)  ── Postgres 16 (data, jobs, sessions, audit)
                     └─ worker  (same codebase, python -m app.worker; PaddleOCR + LLM) ── MinIO
```
- Design-time (workspaces/templates/versions) and runtime (documents/jobs/records/reviews) live in separate tables and API routers. Runtime only reads **published** versions.
- Use named Docker volumes for Postgres and MinIO, not bind mounts. The repo is under OneDrive and bind-mounting DB files there causes corruption and locking issues.

### Repo layout (flat; one file per concern, split only if it grows)
```
docker-compose.yml  Caddyfile  .env.example  README.md  .github/workflows/ci.yml
backend/
  pyproject.toml (uv)  alembic/  Dockerfile (api)  Dockerfile.worker (adds PaddleOCR)
  app/
    main.py config.py db.py models.py schemas.py audit.py cli.py
    security.py        # password hashing, sessions, CSRF, rate limit
    deps.py            # current_user, require_workspace_role(), RLS session vars
    api/ auth.py workspaces.py templates.py documents.py reviews.py jobs.py
    domain/ schema_subset.py rules.py scoring.py versioning.py
    providers/ llm.py ocr.py storage.py     # Protocol + one impl each (+ Fake for tests)
    pipeline/ worker.py stages.py agent.py
  tests/
frontend/
  src/ lib/api.ts (fetch + CSRF + error mapping)  routes/  features/{auth,workspaces,templates,rules,documents,review}/  components/ui (shadcn)
```

---

## Data model (Postgres, Alembic migrations)

| Table | Key columns / notes |
|---|---|
| `users` | id uuid, email citext unique, password_hash, is_platform_admin, is_active, failed_logins, locked_until |
| `sessions` | id, user_id, token_hash (sha256 of opaque token), csrf_token_hash, created_at, last_seen_at, expires_at, ip, user_agent |
| `workspaces` | id, name unique, description, created_by |
| `memberships` | (workspace_id, user_id) PK, role enum(admin, reviewer) |
| `templates` | id, workspace_id, name, purpose, document_type; unique(workspace_id, name) |
| `template_versions` | id, template_id, version_no, status enum(draft, published, archived), system_prompt, json_schema jsonb, rules jsonb, scoring jsonb, agent_generated bool, human_reviewed_by/at, content_hash, published_by/at. **Partial unique: one draft per template.** **Trigger: reject UPDATE/DELETE when status='published'** (except published→archived) |
| `documents` | id, workspace_id, template_version_id (FK, published only, enforced in app + check trigger), storage_key (uuid), sha256, size, page_count, original_filename (display only), uploaded_by, supersedes_document_id |
| `jobs` | id, document_id, kind(runtime/test), config_snapshot jsonb (test only), status (state machine), stage, attempts, max_attempts, next_run_at, locked_until, locked_by, last_error, error_class(transient/permanent) |
| `records` | id, job_id, document_id, template_version_id, ocr jsonb (per-page text, method, confidence), extraction jsonb, extraction_valid, validation_errors jsonb, rule_results jsonb, score numeric, risk_level, contributors jsonb, summary, model_meta jsonb (provider, model, tokens, latency, prompt_hash), evaluated_at |
| `reviews` | id, record_id, reviewer_id, action enum(approve, reject, request_correction), comment, created_at. Insert-only |
| `audit_events` | id bigserial, at, actor_id, workspace_id, entity_type, entity_id, action, data jsonb. **App DB role has INSERT/SELECT only; trigger blocks UPDATE/DELETE** |

**DB roles:** `dociq_owner` runs migrations; `dociq_app` (api + worker) has DML only and no DDL. **RLS** applies to workspace-scoped tables, with policy `workspace_id IN (select workspace_id from memberships where user_id = current_setting('app.user_id'))`, or bypass when `app.is_platform_admin`. The setting is applied per request via `SET LOCAL`. The worker runs as a separate `dociq_worker` role with `BYPASSRLS`, scoped to its tables.

---

## Backend modules

### Security (`security.py`, `deps.py`, `main.py`)
- **Passwords:** argon2id (`argon2-cffi`), minimum 12 characters, maximum 128. No default credentials: the first admin is created with `python -m app.cli create-admin` (password prompted, never taken as an argument).
- **Sessions:** 256-bit random opaque token in a `__Host-session` cookie (`HttpOnly; Secure; SameSite=Strict; Path=/`). Only the sha256 hash is stored. Idle timeout 30 min, absolute 12 h. The token is rotated on login, and logout deletes the row.
- **CSRF:** a per-session token is returned by `/api/auth/me`, sent back in the `X-CSRF-Token` header on every non-GET request, and compared in constant time.
- **Brute force:** per-account lockout (5 failures → 15 min) plus a per-IP limit kept in a small Postgres counter table. No Redis.
- **Authz:** every router depends on `require_workspace_role(roles)`, which resolves the workspace from the path or entity, returns 404 rather than 403 on non-membership (no existence leak), and sets the RLS vars.
- **Caddy headers:** strict CSP (`default-src 'self'; object-src 'none'; frame-ancestors 'none'`), HSTS, `X-Content-Type-Options`, `Referrer-Policy: no-referrer`, and a 25 MB request body cap.
- **Config:** `pydantic-settings` loads all secrets from env (`.env`, gitignored). `.env.example` has placeholders only, and the app refuses to start if a secret is missing or set to a placeholder value.
- **Logging:** structured JSON logs with request IDs. **Never log** OCR text, extraction values, prompts sent with document content, or passwords/tokens.
- **Errors:** a generic error body with a request ID and no stack traces in responses.

### Upload safety (`api/documents.py`, `providers/storage.py`)
- Rejects files over 20 MB, files whose magic bytes are not `%PDF-`, encrypted PDFs, and PDFs over 50 pages. The page count comes from parsing in the **worker**; the API only checks size and magic bytes and streams to storage.
- The storage key is a UUID. The filename is kept for display only, sanitized and never used in a path.
- **AES-256-GCM** encryption (`cryptography`) is applied before the MinIO put, with a per-object random nonce and the key from env. Integrity is checked through the stored sha256.
- Downloads are streamed through the API after an authz check (no presigned URLs) with `Content-Type: application/pdf`, `Content-Disposition: inline`, and `Content-Security-Policy: sandbox`.

### LLM provider (`providers/llm.py`)
- `class LlmProvider(Protocol): def complete_json(system, user, schema, *, max_tokens, timeout) -> LlmResult`
- `OpenAICompatibleProvider` uses the `openai` SDK with `base_url`. It tries `response_format={"type":"json_schema", strict}` first and falls back to `json_object` mode. The output is always validated with `jsonschema`; if invalid, one repair retry sends the validation errors back; after that the output is marked invalid.
- `FakeProvider` exists for tests.
- Timeouts, `max_tokens`, and an input cap (truncate OCR text by page budget, recorded in `model_meta`).
- **Prompt-injection posture:** document text is wrapped in delimiters and labelled untrusted data. The LLM has no tools, and its output only fills schema-validated fields that feed deterministic rules, so the worst case is a wrong value that a reviewer can see.

### OCR provider (`providers/ocr.py`)
- `class OcrProvider(Protocol): def ocr_page(png_bytes) -> OcrPage(text, confidence)`. `PaddleOcrProvider` runs CPU-only in the worker image, and a `FakeOcr` exists for tests.
- Per-page flow: pypdfium2 text layer. If the page has fewer than 20 meaningful characters, it is rasterized at 300 dpi and sent to OCR. The method (`text_layer` or `ocr`) and the confidence are persisted per page.

### Template Agent (`pipeline/agent.py`)
- Input: name, purpose, document type. One LLM call returns `{system_prompt, json_schema}` and is validated by `schema_subset.validate()`. On failure there is one repair retry; after that the errors go back to the UI.
- The saved draft is marked `agent_generated=true`. **Publishing requires `human_reviewed_by` to be set** (an explicit checkbox in the UI), as the BRD requires.

### Schema subset validator (`domain/schema_subset.py`)
- Meta-validates against JSON Schema Draft 2020-12, then enforces the supported subset: types `string, number, integer, boolean, object, array`, plus `string` with `format: date`. It also checks `required`, `description` and `enum`, and requires `additionalProperties:false` on objects. It rejects `$ref` and remote refs and caps depth at 5 and fields at 200.
- `field_paths(schema) -> {path: type}` is the single source the rule validator and the UI field picker use.

### Rule engine (`domain/rules.py`): pure functions, no I/O
- **Rule:** `{id (stable slug), category ∈ {completeness, validation, eligibility, risk, compliance, business}, name, message, weight, condition}`.
- **Condition:** `{all:[...]} | {any:[...]} | {not: c} | {field, op, value?}`.
- **Operators by type:**
  - any type: `exists`, `not_exists`, `is_empty`
  - string: `eq`, `neq`, `in`, `not_in`, `contains`, `matches`
  - number/integer: `eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `between`
  - date: `before`, `after`, `within_days`, `older_than_days`, all relative to `evaluated_at`
  - boolean: `is_true`, `is_false`
  - array: `count_eq`, `count_gte`, `count_lte`, `any_item`, `all_item`, where the item conditions are nested and use relative paths
- **`matches` uses `google-re2`** (linear time, no ReDoS) with a 200-character pattern cap.
- **`validate_rules(rules, schema)`:** every field must exist in `field_paths`, every operator must suit the field type, the value must match the field type, and IDs must be unique. This is enforced at save time and again at publish.
- **`evaluate(rules, data, evaluated_at) -> [RuleResult(rule_id, category, passed, message, actual)]`**. It is total: a missing field gives a defined result, never an exception.

### Scoring (`domain/scoring.py`)
- Config: `{weights: {rule_id: Decimal}, thresholds: {low, medium, high}}`, validated so that `0 ≤ low < medium < high` and every weight key is a known rule.
- `score(results, config) -> (total, risk_level, contributors[])`. The input is the persisted rule results only.

### Versioning (`domain/versioning.py`)
- `get_or_create_draft(template)`: if a draft exists, return it; otherwise clone the latest published version into `version_no+1` as a draft.
- `publish(version)`: re-runs all validators and the human-review check, computes `content_hash`, sets the status to published, and writes an audit event, all in one transaction.

### Pipeline worker (`pipeline/worker.py`, `stages.py`)
- **Claim:** `UPDATE jobs SET locked_by, locked_until=now()+5min ... WHERE id = (SELECT id FROM jobs WHERE status IN ('QUEUED','PROCESSING') AND next_run_at<=now() AND (locked_until IS NULL OR locked_until<now()) ORDER BY next_run_at FOR UPDATE SKIP LOCKED LIMIT 1)`. An expired lease means a crashed worker, and the job is picked up again.
- **Stages, in order:** OCR, EXTRACTION, VALIDATION, RULES, SCORING, SUMMARY, then REVIEW_REQUIRED. Each stage writes its output to `records` and advances `jobs.stage` **in the same transaction**, so a resumed job skips completed stages (idempotent).
- **Failure handling:** a transient error (timeout, 5xx, rate limit) retries with exponential backoff (30 s, 2 m, 8 m) up to `max_attempts=3`. A permanent error (corrupt PDF, config error) sets FAILED. Both write an audit event and `last_error` with a sanitized message.
- `POST /jobs/{id}/retry` (admin) resets attempts on a FAILED job and resumes it from its stage.
- Polling with a 1 s sleep while idle. `LISTEN/NOTIFY` is skipped and can be added if latency matters.

### API surface (FastAPI, OpenAPI auto-docs at `/api/docs`, disabled when `ENV=prod`)
```
POST /api/auth/login | /logout      GET /api/auth/me
GET/POST /api/users (platform admin)
GET/POST/PATCH /api/workspaces      GET/PUT/DELETE /api/workspaces/{id}/members
GET/POST /api/workspaces/{id}/templates      GET/PATCH /api/templates/{id}
POST /api/templates/{id}/generate            # agent → draft
GET /api/templates/{id}/versions             GET /api/versions/{id}
PUT /api/versions/{id}  (draft only: prompt/schema/rules/scoring)   POST /api/versions/{id}/validate
POST /api/versions/{id}/mark-reviewed        POST /api/versions/{id}/publish
POST /api/versions/{id}/test  (multipart PDF) → job id
POST /api/workspaces/{id}/documents (multipart, version_id) → {document_id, job_id}   # returns fast
GET /api/documents/{id}  /file               GET /api/jobs/{id}   POST /api/jobs/{id}/retry
GET /api/workspaces/{id}/jobs?status=FAILED
GET /api/review/queue?workspace&template&risk    GET /api/records/{id}
POST /api/records/{id}/reviews               GET /api/records/{id}/audit
```

---

## Frontend (React + Vite + TS + shadcn/ui)

- **`lib/api.ts`:** a single fetch wrapper (`credentials:'include'`, CSRF header, 401 → login, typed errors). Types are generated from OpenAPI with `openapi-typescript`, so there are no hand-written DTO duplicates.
- **Routing:** React Router. Route guards are driven by `/auth/me` (role per workspace), and the UI hides disallowed actions. The server stays the authority.
- **Screens:**
  1. **Login**
  2. **Workspaces**: list, create (platform admin), members (admin).
  3. **Templates** in a workspace: list, create (name, purpose, doc type).
  4. **Template editor** (tabs, version selector, a read-only banner on published versions plus an "Edit → new draft" button):
     - **Purpose & Generate**: calls the agent and shows a loading state and any errors.
     - **Prompt**: textarea.
     - **Schema**: visual field-tree editor (name, type, required, description, enum, nested object/array) plus a raw JSON tab. Both are validated server-side through `/validate`, with inline errors.
     - **Rules**: grouped by the 6 categories. Each rule row has a field picker fed by `field_paths`, an operator list filtered by field type, and a typed value input, with nested all/any/not groups.
     - **Scoring**: a weight per rule and the three thresholds.
     - **Test**: upload a sample PDF, poll the job, then show the panels for per-page OCR text (method and confidence), extracted JSON, validation errors, rule results, score/risk/contributors and summary.
     - **Publish**: validation checklist, a required "I reviewed the AI-generated configuration" checkbox, then publish.
  5. **Documents**: upload to a published version, list with status chips (polled with TanStack Query `refetchInterval` while the job isn't finished), failed jobs with error and retry.
  6. **Review queue**: table sorted by risk, with filters.
  7. **Record review**: split view. The left side is a PDF viewer (`react-pdf`, blob fetched with credentials). The right side shows the summary, a risk badge with its contributing rules, rule results by category (failed first), the extracted data tree, and a validation flag. Actions are Approve, Reject and Request correction; a comment is required for the last two. An audit timeline sits below.
- **Rendering safety:** all LLM text (summary, prompt) is rendered as plain text. There is no `dangerouslySetInnerHTML` and no markdown-to-HTML, and an ESLint rule bans it.
- **Accessibility:** shadcn/Radix primitives, labelled inputs, keyboard-operable rule builder, and risk shown by colour **and** text.

---

## Build order (single pass; each milestone is gated by its check)

1. ✅ **[DONE] Infra skeleton**: compose (postgres, minio, api, worker, caddy, vite dev), settings, Alembic baseline, DB roles, `/api/health` (DB + MinIO). ✔ `docker compose up` → health 200 over https://localhost.
2. ✅ **[DONE] Auth, tenancy, audit**: users, sessions, CSRF, lockout, memberships, RLS, `audit_events`, `create-admin` CLI. Frontend: login, app shell, workspaces and members. ✔ pytest authz matrix (below) passes.
3. ✅ **[DONE] Templates and versioning**: CRUD, schema subset validator, draft/publish/clone, immutability trigger. Frontend: template list, editor shell, prompt and schema editors. ✔ tests: published UPDATE raises at the DB level; edit-published creates v+1 draft.
4. ✅ **[DONE] LLM provider and Template Agent**. Frontend: Generate. ✔ a real call against the configured endpoint gives a valid schema on the HR onboarding purpose; FakeProvider tests cover repair/invalid paths.
5. ✅ **[DONE] Rules and scoring**: engine, validators, UI builder and scoring tab (shipped as JSON editors + a computed field-reference panel, not a drag-and-drop visual tree — see commit notes). ✔ table-driven tests for every operator/type pair, missing fields and the date reference; scoring golden tests (known results give a known score and risk).
6. ✅ **[DONE] Storage, OCR, pipeline, test harness**: encryption, PaddleOCR worker image, the 6 stages (OCR/EXTRACTION/VALIDATION/RULES/SCORING/SUMMARY), retries and leases. Frontend: Test tab. ✔ verified for real (not just mocked): a real scanned PDF through the real worker — MinIO encrypt/decrypt round trip, PaddleOCR correctly reading an embedded image ("Employee Name: Jordan Rivera" at 99.99% confidence), a real call to api.openai.com failing auth and being correctly classified transient/retried; a killed-worker resume test confirms OCR isn't re-run; a forced failure retries then FAILS then manual retry recovers (automated, FakeLlmProvider).
7. ✅ **[DONE] Publish and runtime ingestion**: upload, jobs list, failure view and retry. ✔ upload returns immediately with a job ID; the record references the exact version ID (verified via the real upload above).
8. **Review**: queue, record view, actions, audit timeline. ✔ the reviewer sees the PDF, data, rules, score, risk and summary; the action is recorded with reviewer and timestamp.
9. **Hardening and docs**: CI (ruff incl. bandit rules, mypy, pytest, `pip-audit`, `tsc`, eslint, `npm audit --audit-level=high`, gitleaks), README (setup, `.env`, create-admin, architecture), and an updated `docs/implementation-plan.md` that points to this plan. ✔ CI green locally with `act` or by running the scripts.

---

## Verification

- **Backend unit tests** (pytest, real Postgres from compose, Fake LLM/OCR): `rules.py`, `scoring.py`, `schema_subset.py`, `versioning.py`.
- **Authz matrix test**: one parametrized test iterates over **every route in the FastAPI app** and asserts that anonymous gets 401, a non-member gets 404, and a reviewer gets 403 on admin routes. It fails automatically when a new route is added without a guard.
- **RLS test**: a raw query as `dociq_app` with another user's `app.user_id` returns 0 rows.
- **Immutability and audit tests**: UPDATE on a published version or on `audit_events` raises at the DB level.
- **Upload tests**: wrong magic bytes, an oversized file and an encrypted PDF are each rejected; the stored object is not plaintext (`%PDF` is absent from the MinIO bytes).
- **One Playwright e2e of the vertical slice**: login → create HR workspace → template from purpose → edit schema → add a completeness and a risk rule → set thresholds → test with a sample PDF → publish → upload → reviewer approves → audit shows the full chain.
- **Manual check** with the user's sample HR onboarding PDFs through the UI.

## Deferred (with trigger to add)
- MFA/TOTP, once it is deployed beyond localhost.
- ClamAV upload scanning, once it accepts files from untrusted users.
- Eval suite (field P/R/F1, risk detection), when labelled data is ready.
- SSO/OIDC, bulk ingestion, version diff viewer, quality analytics (BRD stretch goals).
- Data retention/deletion policy and K8s, once there is real PII or a hosted deployment.
- `LISTEN/NOTIFY` for the worker, if 1 s polling latency matters.
