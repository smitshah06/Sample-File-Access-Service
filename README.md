# Sample File Access Service

A small HTTP service that controls who may download research data files.

A sample has files, and each file has a QC status that a separate pipeline updates. **A user can download a file only if they own the sample or were granted access to it, _and_ the file has passed QC.** When both hold, the service returns a time-limited (fake) download URL.

The full design is in **[docs/implementation-plan.md](docs/implementation-plan.md)**. It covers requirements, diagrams, API, error semantics, alternatives considered, security, tests, CI and the Azure production path. It was written before the code; this README summarises it and notes where the build differs.

## How to run it

Requires Python 3.12.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

The service refuses to start without two secrets (at least 32 characters each, and different from each other):

```bash
export SIGNING_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
export QC_CALLBACK_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
uvicorn app.main:create_app --factory --reload
```

Interactive API docs: <http://localhost:8000/docs>. Optional settings (`DB_PATH`, `PUBLIC_BASE_URL`, `DOWNLOAD_URL_TTL_SECONDS`) are listed in [.env.example](.env.example).

Tests and lint (the same commands CI runs):

```bash
pytest
ruff check . && ruff format --check .
```

### Walkthrough with curl

```bash
# 1. alice registers a sample with one file (QC starts as pending)
curl -s -X POST localhost:8000/samples -H 'Content-Type: application/json' -H 'X-User-Id: alice' \
  -d '{"name": "TCGA-0001", "files": [{"filename": "tumor.bam"}]}'
#    -> 201, note the sample "id" and the file "id"

# 2. alice grants bob access to the sample
curl -s -X POST localhost:8000/samples/$SAMPLE_ID/grants -H 'Content-Type: application/json' \
  -H 'X-User-Id: alice' -d '{"user_id": "bob"}'                                   # -> 201

# 3. bob asks for the file before QC has finished
curl -s -X POST localhost:8000/files/$FILE_ID/download-url -H 'X-User-Id: bob'    # -> 409 qc_pending

# 4. the QC pipeline reports a pass
curl -s -X PUT localhost:8000/internal/files/$FILE_ID/qc-status -H 'Content-Type: application/json' \
  -H "X-Pipeline-Token: $QC_CALLBACK_TOKEN" -d '{"status": "passed"}'             # -> 200

# 5. bob gets a signed URL valid for 5 minutes, and follows it
curl -s -X POST localhost:8000/files/$FILE_ID/download-url -H 'X-User-Id: bob'    # -> 200 {url, expires_at}
curl -s "$URL"                                                                     # -> 200 fake content

# 6. someone without access gets the same answer as for a file that doesn't exist
curl -s -X POST localhost:8000/files/$FILE_ID/download-url -H 'X-User-Id: mallory' # -> 404
```

## API summary

All errors have the shape `{"error": {"code": "...", "message": "..."}}`. Schema validation errors use FastAPI's standard `422` body.

| Endpoint | Purpose | Status codes |
|---|---|---|
| `POST /samples` | Register a sample (owner = caller) with 0..N files, all `pending` | 201, 401, 422 |
| `POST /samples/{id}/grants` | Owner grants a user access to the sample | 201 new, 200 already granted, 400 grant to owner, 401, 403 grantee tried to grant, 404, 422 |
| `PUT /internal/files/{id}/qc-status` | QC pipeline callback: `passed` or `failed` | 200 (`changed: true/false`), 401 bad token, 404, 409 conflicting result, 422 |
| `POST /files/{id}/download-url` | Ask for a time-limited download URL | 200, 401, 404 missing *or* not permitted, 409 `qc_pending` / `qc_failed` |
| `GET /downloads/{id}?user&exp&sig` | Fake storage: accepts only a valid, unexpired signed URL | 200, 403 invalid or expired, 404/409 if access or QC no longer hold |
| `GET /samples/{id}` | Read a sample and its files (owner or grantee) | 200, 401, 404 |
| `GET /healthz` | Liveness/readiness, checks the database | 200 |

## Project layout

```text
app/
  api/            HTTP only: routes.py (handlers + error→status map), deps.py (identity, pipeline token), schemas.py
  domain/         All business rules: service.py (AccessService), models.py, errors.py. No HTTP, no SQL.
  persistence/    schema.sql + repository.py (SQLite, parameterised SQL)
  signing.py      HMAC-SHA256 URL signer
  clock.py        System clock + frozen test clock
  config.py       Settings from env, fail fast on missing/weak secrets
  main.py         create_app() factory
tests/            test_access_service.py (domain rules), test_signing.py, test_api.py
docs/             implementation-plan.md
```

## Key choices

- **Python + FastAPI.** Pydantic validation and OpenAPI docs come for free, and it's quick to build in the time box. The same language as the bioinformaticians who would integrate with it.
- **Layered, with a pure domain.** Every access rule is in `AccessService`, which takes a repository, signer and clock in its constructor. Most tests call it directly with no HTTP. The API layer only translates HTTP ↔ domain, and the mapping from domain errors to status codes lives in one table.
- **SQLite via the standard library, behind a repository.** Data survives restarts, and the database enforces constraints (foreign keys, `UNIQUE`, `CHECK` on QC status). Registration is one transaction. No ORM, so the SQL stays visible.
- **404, not 403, for files you aren't allowed to see.** An outsider can't tell "doesn't exist" from "not yours", so they can't discover which samples and files exist. 403 is used only where the caller already knows the resource exists (a grantee trying to grant).
- **409 when QC hasn't passed.** The caller is permitted, but the file isn't ready. `qc_pending` means retry later; `qc_failed` means never. This is only reachable after the access check, so outsiders never learn a file's QC status.
- **QC is a one-way state machine.** `pending → passed | failed`, then final. Repeating the same result returns 200 with `changed: false`, because pipelines retry. A conflicting result returns 409 and logs a warning. The change is one atomic `UPDATE ... WHERE qc_status = 'pending'`, so two racing callbacks can't both win.
- **HMAC-signed URLs.** The signature covers file ID, user ID and expiry (5 minutes), is compared in constant time, and needs no stored state. It has the same shape as an Azure Blob SAS URL, which is what it would become in production. The fake `/downloads` endpoint re-checks access and QC when the link is used.
- **The QC callback is protected, even though it's "trusted".** It sits under `/internal/` (easy to block at the ingress) and needs an `X-Pipeline-Token`, a separate secret from the signing key, compared in constant time.
- **Audit log.** Grants, QC changes, issued URLs and denials are logged as JSON lines to stdout. Only IDs are logged: never filenames, sample names, signatures, tokens or URLs.

## Alternatives rejected

| Decision | Rejected | Why |
|---|---|---|
| Language | TypeScript + Fastify | Equally valid; more setup for the same result in the time box. The design ports one-to-one. |
| Persistence | In-memory dicts | Lost on restart; constraints and atomicity would have to be hand-written. |
| Persistence | PostgreSQL now | The right production choice, but needs Docker in CI and locally. The repository is the seam to swap it in. |
| Download URL | JWT in the URL | Extra dependency and algorithm-confusion pitfalls, with no security gain over HMAC here. |
| Download URL | Opaque token stored in DB | Revocable, but needs a DB write per request plus cleanup. A short TTL plus the re-check at use covers most of the benefit. |
| Download URL | Real Azure Blob SAS | The production target, but reviewers couldn't run it locally, and the spec asks for a fake URL. |
| Identity | JWT / Entra ID now | Too much setup for the time box. Only `get_caller_id()` would change. |
| Identity | Per-user API keys | Long-lived secrets in users' hands; worse than SSO. |
| Access model | RBAC / groups | Overkill for "share a sample with a person". Grants can later reference groups. |
| QC transport | Service Bus / polling | The spec describes a callback. The idempotent handler is exactly what a queue consumer would call. Polling is slow and wasteful. |
| Architecture | Microservices | Network hops inside one authorization decision, and N deployments for a small team. |

## Assumptions

- **Identity** is the `X-User-Id` header (pattern `^[A-Za-z0-9._@-]{1,128}$`). Missing or malformed → 401. There is no user registry: users belong to the identity provider, and an unknown user is simply someone with no access.
- **The owner is the caller** who registers the sample. The request body can't set another owner.
- **Only the owner can grant.** The owner always has access, and granting to the owner is a 400.
- **Grants are per sample** and cover all of its files, including files whose QC passes later.
- **QC results are final.** `pending` can't be set by the callback (422). Re-running QC would be a separate, audited operation.
- **Files are only added at registration.** Filenames are unique within a sample, and zero files is allowed.
- **The pipeline identifies files by their UUID**, which is returned at registration.
- **"Time-limited" means 5 minutes** (configurable).
- **No grant revocation or deletion** in this build.
- **Names may be sensitive** (research data), so only IDs appear in URLs and logs.

## Known weaknesses

1. **The identity header is spoofable.** Anyone can claim to be anyone. This is the biggest gap, and it is deliberate given that auth is out of scope.
2. **The QC callback uses a static shared secret.** It has no per-caller identity and no rotation.
3. **Signed URLs are bearer tokens.** A leaked URL works for anyone until it expires (≤ 5 min). The `user` parameter records who it was issued to; it doesn't authenticate whoever uses it.
4. **No grant revocation, sample deletion, or adding files after registration.**
5. **SQLite has a single writer on a single node**, so the service can't run as multiple replicas.
6. **One signing key with no rotation.** Rotating it invalidates outstanding URLs.
7. **QC results can't be corrected** without touching the database.
8. **The audit log goes to stdout only.** It isn't tamper-evident, and it's lost if nothing collects it.
9. **No rate limiting or request-size limits in the app.**
10. **The schema is created at startup**, with no migrations.

## What I'd do with more time

In priority order:

1. **Microsoft Entra ID auth.** Validate bearer tokens and use the `oid` claim; only `get_caller_id()` changes. The QC pipeline would use a managed identity with a `QC.Write` app role instead of the shared token.
2. **PostgreSQL** (Azure Flexible Server, private endpoint, Entra auth) with Alembic migrations, via a `PostgresRepository`.
3. **Real Azure Blob user-delegation SAS URLs** (read-only, one blob, 5 minutes) in place of `UrlSigner`, so large files never pass through the service.
4. **Grant revocation** (`DELETE /samples/{id}/grants/{user}`), plus an audited admin path to correct QC.
5. **Azure Service Bus** for QC results, with a consumer calling the same `update_qc`, plus dead-lettering and alerts.
6. **CI hardening.** Pin actions by SHA, add Dependabot, pip-audit, CodeQL, gitleaks, and mypy on the domain. Build a Docker image (multi-stage, non-root), scan it with Trivy, produce an SBOM and sign it. Push to ACR using GitHub OIDC.
7. **Deploy to AKS.** Helm with probes on `/healthz`, HPA, NetworkPolicy, Key Vault via the CSI driver, and a staging → prod approval gate.
8. **Observability.** OpenTelemetry to Application Insights, audit logs to Log Analytics with long retention, and alerts on 5xx, dead-letter queue depth, and spikes in `download_denied`.

Details and a diagram are in [§7–8 of the implementation plan](docs/implementation-plan.md#7-cicd).

## Differences from the plan

- `clock.py` was added as its own module (the plan listed the clock as a component but not as a file).
- There are 47 tests instead of the ~28 planned: several planned cases became parametrised tests, and there are extra checks for validation, visibility and URL shape.
- Nothing on the plan's cut line had to be dropped.

## Use of AI tools

This project was planned and built with an AI coding assistant (Claude Code). The design decisions, assumptions and trade-offs were reviewed and agreed before implementation (see the plan), and the code was reviewed and tested before submission.
