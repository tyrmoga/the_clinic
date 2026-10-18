# Section 4: AI reflection

Back to the [README](../README.md).

Answer the four questions below. Bullet points are fine.

## 1. What did you use AI for across the four sections?

_Answer:_ A sounding board on the workload (read-heavy, TOCTOU as the main write risk, scalability, consistency and availability tradeoffs, rough throughput and latency estimates).

## 2. Give one example where an AI suggestion improved your work. What did you prompt it with?

**What I prompted:** I asked for a full-repo audit from the point of view of a senior developer who just inherited the code and would be paged when it breaks. The prompt required the order correct, safe, holds under load, tested, fast, lean; a read of the README, deployment/build config, dependencies, entry points and tests; a trace of the main data flows; and a report with concrete file and line references plus the smallest fix for each finding.

**What improved:** The audit found six must-fix issues: the integration tests could delete the live booking table, production accepted empty or five-character JWT secrets, unhandled database errors logged personal data and password hashes, NUL bytes in names or cancel reasons caused 500s, `/readyz` returned 200 while migrations were missing and Render checked `/healthz`, and the deploy job could be green while skipping migrations and the deploy. I fixed them with a separate `TEST_DATABASE_URL` and an `_test` database guard, 32-character production secret validation at app startup, `hide_parameters=True` plus a `DataError` to 422 handler, NUL-byte validation, an `alembic_version` readiness check with `/readyz` as the Render health path, and CI steps that fail when secrets are missing and verify `/readyz` plus a demo login after deploying. The audit also led to the daily idempotency cleanup workflow, a bounded rate limiter, separation of migration settings from app secrets, safer demo instructions, an idempotency race test, cached-slot re-filtering, dead-code removal, and the switch to `httpx2`. The only item left was moving the demo to a paid Render plan, which was explicitly out of scope.

## 3. Give one example where AI output was wrong or incomplete and how you caught it.

**What the AI produced:** _Answer:_

**How I caught it:** _Answer:_

**What I changed:** _Answer:_

## 4. Name two decisions you made without AI. Why did you trust your own judgment there?

1. **Decision:** _Answer:_
   **Why I trusted my own judgment:** _Answer:_
2. **Decision:** _Answer:_
   **Why I trusted my own judgment:** _Answer:_
