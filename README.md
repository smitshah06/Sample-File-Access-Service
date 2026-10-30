# Sample File Access Service

A web application that controls who may download research data files.

A sample has files, and each file has a QC status that a separate pipeline updates. **A user can download a file only if they own the sample or were granted access to it, _and_ the file has passed QC.** Users sign in, upload and manage their samples' files, and share them. Admins manage users and roles and can grant or revoke access on any sample.

> **Time box.** The exercise allowed 2–3 hours. **All of this was built inside it: just under 3 h in total (about 2 h 58 min)**, including planning and documentation.
> - **Required scope first.** I delivered the spec's required scope, fully tested, and tagged it **[`v1-timeboxed`](../../tree/v1-timeboxed)** as a checkpoint.
> - **Then the extension**, with the remaining time: the front end, sign-in, roles, admin dashboard, large-file uploads of any type, and revocation.
>
> The timeline and what each phase added are in [implementation plan §0](docs/implementation-plan.md#0-scope-and-history) and [§10](docs/implementation-plan.md#10-implementation-plan-and-time-budget).

The full design is in **[docs/implementation-plan.md](docs/implementation-plan.md)**: requirements, diagrams, API, error semantics, roles, alternatives considered, security, tests, CI and the Azure production path.

## How to run it

Requires **Python 3.12** and **Node 20+** (for the front end only).

> **API only?** Skip every `frontend` step: the API, `/api/docs` and the curl / `scripts/simulate_qc.py` flow work with Python alone.

### 1. Configure

```bash
cp .env.example .env
```

Fill in `.env`:
- `SIGNING_SECRET` and `QC_CALLBACK_TOKEN`: generate each with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Each must be at least 32 characters, and they must be different.
- `ADMIN_PASSWORD`: at least 12 characters. The first admin is created from `ADMIN_USERNAME` / `ADMIN_PASSWORD` on first start.
- For local http only, also uncomment `COOKIE_SECURE=false`, otherwise some browsers won't send the session cookie over plain http. Every other optional setting is commented out with its default.

Then export the file into your shell:

```bash
set -a; source .env; set +a
```

### 2a. Development: two terminals, hot reload

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:create_app --factory --reload            # API on :8000
```

```bash
cd frontend && npm install && npm run dev                 # UI on http://localhost:5173 (proxies /api to :8000)
```

### 2b. Production-style: one process serves UI + API

```bash
(cd frontend && npm ci && npm run build)
uvicorn app.main:create_app --factory                     # UI and API on http://localhost:8000
```

### 2c. Docker

```bash
docker build -t sample-access .
docker run --env-file .env -p 8000:8000 -v sample-data:/data sample-access
```

*Data lives in the `/data` volume. API docs are off in the image (`ENABLE_DOCS=false`). CI builds this image on every push (the `docker` job); it hasn't been built on the author's machine, which has no Docker.*

### Tests and lint (the same commands CI runs)

```bash
pytest && ruff check . && ruff format --check .
```

```bash
cd frontend && npm run lint && npm run typecheck && npm test -- --run && npm run build
```

> Upgrading from an earlier version? Delete the old `sample_access.db`. The schema changed and there are no migrations yet.

## Using it

1. Sign in as the admin. Under **Users**, create a couple of accounts (for example `alice` and `bob`).
2. Sign in as **alice**, **Register sample**, open it and **Upload** a file of any type: video, PPTX, audio, PDF, CSV, text, anything up to 10 GB by default (`MAX_UPLOAD_MB`). A progress bar shows the chunks going up. If the upload is interrupted, choose the same file again to resume. The file shows as `pending`, and Download is disabled.
3. Play the QC pipeline. Each file's ID is shown under its name on the sample page:

   ```bash
   python scripts/simulate_qc.py <file_id> passed
   ```

4. Download now works for alice. Under **Sharing**, grant `bob`; he sees the sample and can download.
5. As the admin, open **All samples**, pick the sample and **Revoke** bob. Any download link he already had stops working.
6. **Audit log** shows every step.

## Roles

| Action | Owner | Grantee | Admin (no grant) | Anyone else |
|---|---|---|---|---|
| See sample and file list | ✅ | ✅ | ✅ | 404 |
| Download a QC-passed file | ✅ | ✅ | 404 until they grant themselves (audited) | 404 |
| Upload / delete files, delete sample | ✅ | 403 | 403 | 404 |
| Grant / revoke access | ✅ | 403 | ✅ | 404 |
| Manage users and roles, audit log | 403 | 403 | ✅ | 403 |

## API summary

All routes are under `/api`, and errors are `{"error": {"code", "message"}}`. Every state-changing request (anything but GET, HEAD and OPTIONS) except `/api/internal/*` must send `X-Requested-With: fetch` (the CSRF check). Interactive docs are at `/api/docs`.

| Endpoint | Purpose | Main status codes |
|---|---|---|
| `POST /api/auth/login` · `POST /api/auth/logout` · `GET /api/auth/me` | Session sign-in / out / who am I | 200 · 204 · **401 for every failed login** |
| `GET /api/samples` · `POST /api/samples` | My samples (owned + shared) · register | 200 · 201, 422 |
| `GET` / `DELETE /api/samples/{id}` | Read (owner, grantee, admin) · delete (owner) | 200/204, 403, 404 |
| `POST /api/samples/{id}/uploads` | Start a resumable upload (owner): name, size, type | 201, 403, 404, 409, 422 bad filename, **413** over cap, **507** disk full |
| `PUT /api/uploads/{id}?offset=N` · `GET /api/uploads/{id}` | Send one raw chunk (≤ 16 MB) · where to resume | 200, 409 `offset_mismatch`, 413 |
| `POST /api/uploads/{id}/complete` · `DELETE /api/uploads/{id}` | Finish (SHA-256, becomes a file) · cancel | 201 · 204 |
| `DELETE /api/files/{id}` | Delete a file (owner) | 204, 403, 404 |
| `GET` / `POST /api/samples/{id}/grants` | List / grant by username (owner or admin) | 200, 201/200, 400, 403, 404 |
| `DELETE /api/samples/{id}/grants/{user_id}` | Revoke (owner or admin) | 204, 403, 404 |
| `PUT /api/internal/files/{id}/qc-status` | QC pipeline callback (`X-Pipeline-Token`) | 200 `changed`, 401, 404, **409**, 422 |
| `POST /api/files/{id}/download-url` | 5-minute signed URL | 200, **404** missing or not permitted, **409** `qc_pending` / `qc_failed` |
| `GET /api/downloads/{id}?user&exp&sig` | Stream the file (the URL is the credential); supports `Range` | 200/206, 403 invalid or expired, 404/409 if no longer allowed |
| `GET/POST /api/admin/users` · `PATCH /api/admin/users/{id}` | User and role management | 200/201, 400, 403, 409, 422 |
| `GET /api/admin/samples` · `GET /api/admin/audit` | All samples · audit log | 200, 403 |

## Project layout

```text
app/
  api/          HTTP only: routes_auth / routes_samples / routes_admin, deps.py (session → caller), errors.py (error → status), schemas.py
  domain/       All rules: service.py (AccessService), users.py (UserService), audit.py, models.py, errors.py
  persistence/  schema.sql + repository.py (SQLite, parameterised SQL)
  storage.py    FileStore + LocalDiskStore (chunk writes at offsets, SHA-256, atomic finalize)
  signing.py    HMAC-SHA256 URL signer
  main.py       create_app(): CSRF middleware, /api router, admin seed, serves the built SPA
frontend/       Vite + React + TypeScript (api.ts, upload.ts chunked uploader, auth.tsx, pages/, components/)
scripts/        simulate_qc.py
tests/          domain, uploads, auth, admin, API and signing tests
docs/           implementation-plan.md
```

## Key choices

- **Every rule lives in plain Python.** `AccessService` holds every sample/file/sharing rule and `UserService` every account rule. Both are tested directly without HTTP. The API layer only translates, and domain errors map to status codes in one table.
- **404, not 403, for things you can't see.** Outsiders can't tell "doesn't exist" from "not yours". 403 is used only when the caller can already see the resource (a grantee trying to share, a user on an admin page).
- **409 when QC hasn't passed** (`qc_pending` / `qc_failed`). It is only reachable after the access check, so outsiders never learn QC status.
- **QC is a one-way state machine.** Repeats are idempotent, conflicts return 409, and the update is one atomic compare-and-set.
- **HMAC-signed 5-minute URLs, re-checked on use.** Revoking a grant, or deleting the file or sample, stops links that were already issued.
- **Admins manage access, not data.** They see every sample and can grant or revoke, but they download only with an explicit grant, which is audited as `as_admin=true`.
- **Sessions, not JWTs.** A random token sits in an `HttpOnly`, `SameSite=Strict`, `Secure` cookie; only its hash is stored. Logout, deactivation and role changes take effect immediately.
- **Passwords** use scrypt from the standard library. Every failed login gets an identical 401, with matching timing. Five failures lock the account for 15 minutes.
- **CSRF** is handled by `SameSite=Strict` plus a required `X-Requested-With` header, with no CORS.
- **Any file type, served safely.** Video, audio, PDF, PPTX, CSV, text, genomics formats: the service stores bytes and never interprets them. Files are stored under a server UUID (the client's name never touches the filesystem) and always served as an `octet-stream` attachment with `nosniff`, so even an uploaded HTML file can't run in the browser.
- **Large files as resumable chunked uploads** (10 GB local cap):
  - the client declares the size up front, so the cap (413) and free disk (507) are checked before any bytes arrive
  - the file then goes up in 16 MB raw chunks, so memory per request is bounded and each byte is written once
  - an interrupted upload resumes from the last stored byte
  - downloads support HTTP Range
  
  It's the same shape as Azure block blobs, which is the production path.
- **One process, one origin.** FastAPI serves the built React app at `/`, so there's no CORS and the cookie can stay `SameSite=Strict`.
- **Audit everything security-relevant**, to stdout and to a table admins can browse. Only IDs are recorded, never filenames, sample names, tokens or URLs.

## Alternatives rejected

| Decision | Rejected | Why |
|---|---|---|
| Backend | TypeScript + Fastify | Equally valid; Python is quicker here and is the bioinformaticians' language. |
| Front end | Server-rendered (Jinja + htmx) | Further from the team's TypeScript practice; awkward for the admin UI. |
| Identity | `X-User-Id` header (phase 1) | Spoofable. Replaced. |
| Identity | JWT in `localStorage` | Readable by any XSS, and can't be revoked before expiry. |
| Identity | Entra ID now | Reviewers couldn't run it without a tenant. It's the production target, and only `get_current_user()` changes. |
| Passwords | bcrypt / plain SHA-256 | bcrypt has a 72-byte limit and isn't memory-hard; plain SHA-256 is far too fast. argon2id is a fine alternative but adds a compiled dependency. |
| Persistence | In-memory / PostgreSQL now | In-memory loses data and constraints; PostgreSQL is the production target behind the repository. |
| Download URL | JWT / DB tokens / real SAS | No gain / extra writes / needs Azure. SAS is the production target. |
| Access model | Full RBAC with groups | Overkill for now. Grants can later name Entra groups. |
| QC transport | Service Bus / polling | The spec describes a callback, and the handler is already idempotent for a queue later. |
| Architecture | Microservices / separate front-end hosting | Network hops inside authorization, and CORS, for no benefit at this size. |

## Assumptions

- Accounts are created by admins (no self-sign-up). The first admin is seeded from env.
- The owner is whoever registers the sample. Grants are per sample and name an existing username.
- The owner or an admin can grant and revoke. Grantees can read and download only.
- Admins don't get implicit data access.
- QC results are final; the callback can't set `pending`.
- Files can be uploaded at any time (owner only), with unique names per sample. Files registered by name only are kept for spec compatibility; downloading one returns a placeholder.
- Deactivating a user or changing their role signs them out everywhere at once.
- Names may be sensitive, so only IDs appear in URLs and audit records.

## Security notes

- **Protected:** sign-in (scrypt, lockout, no enumeration), sessions (HttpOnly / Strict / Secure, server-side, short-lived), CSRF, authorization in one place, upload and download handling, constant-time comparisons, fail-fast secret checks, audit trail. CI needs no secrets.
- **Logs:** the audit log records IDs only, and the access log redacts download signatures (`sig=REDACTED`). Signed URLs are credentials and never appear in logs.
- **Headers:** every response the app handles (including errors and CSRF rejections) carries `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`; the UI also gets a strict `Content-Security-Policy` (same-origin only). Set `ENABLE_DOCS=false` to turn off `/api/docs` in production.
- **Errors:** every error, including validation (422), unknown routes or methods (404/405) and unexpected failures (500), has the same shape `{"error": {"code", "message"}}`, with no stack traces.
- **Dependencies:** pinned, and audited in CI (`pip-audit`, `npm audit --omit=dev`). GitHub Actions are pinned to commit SHAs.
- **Placeholders:** local accounts stand in for Entra ID; the QC token stands in for managed identity or Service Bus.

## Known weaknesses

1. **Local accounts**: no SSO, MFA or self-service password reset.
2. **Static shared token** for the QC callback.
3. **File bytes pass through the API** and live on local disk (single node, 10 GB cap). Completing a 10 GB upload hashes it inside one request (about 10–30 s).
4. **No malware scanning** of uploads. They are never rendered on our origin, but they can be downloaded.
5. **SQLite**: single writer, single node, and no migrations (upgrading from v1 means a fresh DB).
6. **No rate limiting** beyond login lockout. Lockout itself can be abused to lock out a known user.
7. **A signed URL is a bearer token** for up to 5 minutes. Revocation stops it, but a leak before then can't be told apart from the legitimate user.
8. **QC results can't be corrected** without touching the DB.
9. **The audit log lives in the same database** as the data.
10. **The container image is built in CI but not scanned** (no Trivy or SBOM yet).
11. **Any signed-in user can tell whether a username exists**: granting access to an unknown name returns `user_not_found`. Accepted: usernames aren't secret inside an organisation, and owners need that feedback.
12. **Upload edge cases**:
    - Two simultaneous requests for the same chunk offset with *different* bytes can leave the loser's bytes in the file. Only the uploader can do this, to their own file, and the SHA-256 reflects what was stored.
    - The free-disk check is check-then-act, so concurrent uploads can each pass it.
    - Abandoned uploads are purged when the next upload starts, not on a timer.
13. **No pagination** on the sample, user and grant lists. The audit log is limited.
14. **One moderate advisory remains in the Vite *dev server*** (`server.fs.deny` bypass; fixed in a release too new to adopt yet). It doesn't affect the production build, and the dev server only listens on localhost.

## What I'd do with more time

1. **Microsoft Entra ID** sign-in with MFA and app roles. Replace `get_current_user()` and drop local passwords.
2. **Azure Blob Storage for files of any size** (up to ~190 TiB per blob): clients upload and download directly with short-lived SAS URLs, in parallel and resumable blocks. The API only issues the SAS and records the result. **Defender for Storage** scans before files become downloadable, and lifecycle tiers keep cost down. See [plan §8.1](docs/implementation-plan.md#81-scaling-file-size-and-throughput).
3. **PostgreSQL** + Alembic migrations.
4. **CI hardening** beyond what's there (SHA-pinned actions, `pip-audit`, `npm audit`, Docker build): Trivy image scan, SBOM and image signing, Dependabot, CodeQL, and push to ACR via OIDC.
5. **Deploy to AKS** behind App Gateway + WAF, with Key Vault, probes, HPA and NetworkPolicy.
6. **Service Bus** for QC results; managed identity instead of the shared token.
7. **Observability**: OpenTelemetry → App Insights, audit events → Log Analytics, and alerts on lockouts, denied-download spikes and dead-lettered messages.
8. **Product**: group-based sharing, an audited QC override, per-user quotas, rate limiting, pagination, a timer to purge abandoned uploads, and Playwright end-to-end tests in CI.

Details and a diagram are in [implementation plan §8](docs/implementation-plan.md#8-production-path-on-azure-not-built).

## Use of AI tools

This project was planned and built with an AI coding assistant (Claude Code), which is what made the full scope possible within the time box. The design decisions, assumptions and trade-offs were agreed before implementation (see the plan), and the code was reviewed and tested before submission.
