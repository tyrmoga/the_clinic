# High-level design

Back to the [README](../README.md). Internals are in the
[Low-level design](low-level-design.md); the schema is in
[Data model](data-model.md); deployment is in [Deployment](deployment.md). The
rendered component diagram is in [Diagrams](diagrams.md#5-component-diagram).

```
Patient (web/mobile)
   │  HTTPS / JSON + Bearer token
   ▼
Render edge                              ← managed TLS, routing, HTTPS URL
   │
   ▼
FastAPI web service (Render)             ← auth, input validation, business logic,
   │                                        transactions; in-process 5–10 s cache
   │  SQL (async driver, bounded pool, private network)
   ▼
Render Postgres                          ← source of truth; partial unique index
   │                                        arbitrates booking; idempotency table;
   │                                        not publicly reachable
   ├─ Alembic migrations (pre-deploy)
   └─ managed backups / PITR (paid plan)
```

Each component earns its place against a requirement/number:
- **Render edge** — terminates HTTPS with a managed certificate and gives a
  public URL; no host, TLS, or firewall work. Render does not provide a built-in
  edge rate limiter, so limits run in the app; Cloudflare can be placed in front
  if edge/WAF protection is needed ([decision #10](decisions.md)).
- **FastAPI (stateless)** — all endpoints, strict input validation, bearer-token
  verification, and the process-local 5–10 s availability cache
  ([decision #9](decisions.md)). Scale = add an instance; there is no session
  state to lose.
- **Render Postgres** — the single source of truth; the **partial unique index**
  makes "no double-booking" correct under concurrency, not app logic. The 24 h
  idempotency store is a plain table — no queue, no Redis. The waitlist is
  deferred and its tables are not created
  ([Future extensions](future-extensions.md)).
- **Backups** — managed Render Postgres backups/PITR on a paid plan. The free
  database expires after 30 days and has no backups, so it is only suitable for a
  short review window ([Deployment](deployment.md#free-tier-reality)).
- **Alembic migrations** — schema changes are versioned and run as a pre-deploy
  step ([Deployment](deployment.md#cicd)).
