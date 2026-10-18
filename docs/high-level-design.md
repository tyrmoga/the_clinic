# High-level design

Back to the [README](../README.md). Internals are in the
[Low-level design](low-level-design.md); the schema is in [Data model](data-model.md).

```
Patient (web/mobile)
   │  HTTPS / JSON + Bearer token
   ▼
Render edge: TLS; rate limit in app middleware / CDN  (managed: Render)
   │
   ▼
FastAPI app (stateless, 1–2 instances)   ← auth, business logic, validation, transactions;
   │                                        in-process 5–10 s availability cache
   │  SQL (async driver, bounded pool)
   ▼
PostgreSQL (managed)                     ← source of truth; unique index arbitrates booking;
   │                                        waitlist + idempotency tables live here too
   └─ Alembic migrations (run on deploy)
```

Each component earns its place against a requirement/number:
- **Edge / TLS (Render)** — terminates HTTPS; gives a public URL; forwards to the
  app. The rate limits that keep retry storms and brute-force login off Postgres
  ([decision #10](decisions.md)) run in the app's middleware, or in a CDN/WAF
  placed in front — confirm against Render's current features.
- **FastAPI (stateless)** — all endpoints, validation, and bearer-token
  verification; also holds the process-local 5–10 s availability cache
  ([decision #9](decisions.md)). Horizontal scale = add instances, so there's no
  session state to lose (each instance keeps its own cache window).
- **PostgreSQL** — the single source of truth; the **partial unique index** is
  what makes "no double-booking" correct under concurrency, not app logic. The
  waitlist and 24 h idempotency store are plain tables here — no queue, no Redis.
- **Alembic migrations** — schema changes are versioned and applied as a deploy
  step (needed by CI/CD in Section 3).
