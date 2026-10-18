# Section 4: AI reflection

Back to the [README](../README.md).

Answer the four questions below. Bullet points are fine.

## 1. What did you use AI for across the four sections?

_Answer:_ 
AI was used across all four sections. In Section 1 it was a sounding board for the
read-heavy workload, the TOCTOU (time-of-check/time-of-use) race on booking,
consistency and availability trade-offs, and rough throughput and latency
estimates; it also helped turn that reasoning into diagrams and decision records.
In Section 2 it reviewed the FastAPI/Postgres design, test cases, and edge cases.
In Section 3 it helped with the Render service, migrations, and GitHub Actions
pipeline. In Section 4 it ran the repo audit and helped me tighten this
reflection. I kept the final scope and product design decisions.

## 2. Give one example where an AI suggestion improved your work. What did you prompt it with?

**What I prompted:** I asked for a full-repo audit from the point of view of a senior developer who just inherited the code and would be paged when it breaks. The prompt required the order correct, safe, holds under load, tested, fast, lean; a read of the README, deployment/build config, dependencies, entry points and tests; a trace of the main data flows; and a report with concrete file and line references plus the smallest fix for each finding.

**What improved:** The audit found six must-fix issues: the integration tests could delete the live booking table, production accepted empty or five-character JWT secrets, unhandled database errors logged personal data and password hashes, NUL bytes in names or cancel reasons caused 500s, `/readyz` returned 200 while migrations were missing and Render checked `/healthz`, and the deploy job could be green while skipping migrations and the deploy. I fixed them with a separate `TEST_DATABASE_URL` and an `_test` database guard, 32-character production secret validation at app startup, `hide_parameters=True` plus a `DataError` to 422 handler, NUL-byte validation, an `alembic_version` readiness check with `/readyz` as the Render health path, and CI steps that fail when secrets are missing and verify `/readyz` plus a demo login after deploying. The audit also led to the daily idempotency cleanup workflow, a bounded rate limiter, separation of migration settings from app secrets, safer demo instructions, an idempotency race test, cached-slot re-filtering, dead-code removal, and the switch to `httpx2`. 

## 3. Give one example where AI output was wrong or incomplete and how you caught it.

**What the AI produced:** The first `/readyz` check only ran `SELECT 1`. It
returned 200 even when the deployed database had no tables.

**How I caught it:** The live app returned 500 on login, but `/readyz` returned
200. That mismatch showed the check was incomplete.

**What I changed:** `/readyz` now checks that migrations have run
(`alembic_version`), and both the Render health check and CI use it. CI also
verifies a demo login after deploy.

## 4. Name two decisions you made without AI. Why did you trust your own judgment there?

1. **Decision:** Introduce input validation, idempotency keys and rate limiting even    though the AI classified them as optional hardening.
   **Why I trusted my own judgment:** A public booking API needs both. The code
   is small, and duplicate bookings or abuse are worse than the extra
   maintenance.

2. **Decision:** Reject cancelling an appointment after it has started.
   **Why I trusted my own judgment:** Freeing a past slot has no product value
   and makes the rules ambiguous. I set the clinic rule first and had the
   implementation follow it.
