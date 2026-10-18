# Deployment (Render)

Back to the [README](../README.md). This is the target deployment for the
assessment: Render web service + Render Postgres, with GitHub Actions running
tests and a deploy on merge to `main`.

## Topology

```
Internet
   │  HTTPS
   ▼
Render edge ── managed TLS, routing
   │
   ▼
FastAPI web service ── uvicorn/gunicorn, 1 instance on the free tier
   │  SQL over Render's private network, bounded pool
   ▼
Render Postgres ── source of truth; not publicly reachable
   │
   └─ managed backups / PITR on paid plans

GitHub Actions ── tests on PR ── deploy on merge to main ──> Render
```

Render manages the host, TLS certificates, and the network. The app owns
validation, auth, rate limiting, and migrations.

## Render resources

| Resource | Setting |
|---|---|
| Web service | Python runtime, build `pip install -r requirements.txt`, start `uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips="*"` |
| Health check | `/healthz` |
| Postgres | Internal connection URL; paid plan recommended |
| Environment | Set in the Render dashboard, never in git |
| Auto-deploy | On, branch `main` |
| Pre-deploy | `alembic upgrade head` — paid web services only |

Environment variables: `DATABASE_URL`, `JWT_SECRET`, `JWT_TTL_MINUTES`,
`APP_ENV`, `AVAILABILITY_CACHE_TTL_SECONDS`, `RATE_LIMIT_AUTH_PER_MINUTE`,
`RATE_LIMIT_BOOKING_PER_MINUTE`, `TRUSTED_HOSTS`.

`DATABASE_URL` comes from the Render Postgres internal URL. `JWT_SECRET` is a
generated value; rotate it by deploying a new secret and accepting the old token
window if needed. The **external** URL (used from CI for migrations/seed) requires
TLS; enable it for your driver (e.g. `sslmode=require` for libpq,
`ssl=require` for asyncpg).

## Free-tier reality

- **Free web service:** 512 MB RAM / 0.1 CPU, spins down after 15 minutes of
  inactivity, and takes roughly a minute to wake. 750 instance hours per month.
- **Free Postgres:** 1 GB storage, expires **30 days after creation**, no
  backups, no managed pooling.
- **Paid Postgres ($6+/month):** removes the 30-day expiry and adds backups/PITR.
- **Paid web service ($7+/month):** removes the sleep/cold start.
- **Login cost:** argon2id is tuned for 0.1 CPU / 512 MB. Expect a few hundred
  milliseconds per hash; hashing runs in a worker thread so other requests are
  not blocked. Re-benchmark on the deployed instance.

For the assessment, the free web service is usable, but the evaluator's first
request may be slow. If the review window may extend past 30 days, use a paid
Postgres plan; otherwise the free database will expire. A reasonable minimum is
free web + paid Postgres.

## Migrations and seed

- **Paid web service:** set the Render pre-deploy command to
  `alembic upgrade head`; it runs before the new version receives traffic.
- **Free web service:** Render does not support pre-deploy commands on free
  instances. Run the migration from the GitHub Actions deploy workflow against
  the Postgres **external** URL (stored as a repository secret), then trigger the
  deploy. Do not run migrations at app startup with multiple instances.
- Migrations must be backward-compatible for one release (expand/contract), so a
  failed deploy does not leave the old version broken.
- After the first deploy, run the idempotent seed once from a trusted local or CI
  environment using the external `DATABASE_URL`.
- Seed creates doctors `1`–`5` and `demo1@example.com` … `demo5@example.com` with
  password `DemoPass123!` (demo environment only).

## CI/CD

Branch: `main`. The workflow lives in `.github/workflows/ci.yml`.

On every pull request:
1. `ruff check`
2. `pytest -q` against a Postgres service container
3. Build/install check

On merge to `main`:
1. Run the same tests.
2. If the web service is on the free tier, run `alembic upgrade head` against the
   Postgres **external** URL (repository secret) before deploying.
3. Deploy through Render auto-deploy (branch protection requires the PR checks)
   or call a Render deploy hook from the workflow after tests pass.
4. On a paid web service, the pre-deploy command runs migrations.
5. Render keeps the previous deploy for rollback if the health check fails.

## App-level hardening

Render removes host/TLS work, but the API still has to defend itself:

- Input validation: strict Pydantic schemas, bounded lengths, timestamp
  normalization (naive timestamps use the doctor's timezone), unknown fields
  rejected (except `start_time` and a booking-only `patient_id` assertion).
- Auth: argon2id with parameters tuned for the 0.1 CPU / 512 MB free instance;
  hash and verify in a worker thread so the event loop stays responsive.
  Benchmark login p95; lower memory/time cost or upgrade the instance if needed.
- Short-lived JWTs, owner checks returning `404`.
- Rate limiting: in-app token buckets per patient/IP, tighter for `/auth/*`;
  optionally put Cloudflare in front for edge/WAF protection. Behind Render,
  uvicorn's `--proxy-headers --forwarded-allow-ips="*"` lets per-IP limits see
  the client address; this is safe because the app is only reachable through
  Render's proxy.
- Body-size and request timeouts at the app layer.
- CORS disabled by default; allow only the exact frontend origin if one is added.
- Security headers on responses.
- Postgres uses the internal URL only; no public database access.
- Secrets live in Render environment variables; no `.env` in git.
- Do not log tokens, passwords, or request bodies containing them.
- `/healthz` for liveness and `/readyz` for database connectivity.

## Backups and recovery

- Paid Render Postgres: managed backups and point-in-time recovery; verify the
  retention window on the chosen plan.
- Free Render Postgres: no backups. Treat it as disposable demo data.
- Target RPO ~5 minutes and RTO < 1 hour on a paid plan; confirm the actual
  retention before relying on it.
- Restore drill into a scratch database before submission.

## Demo data and access

Seed command (idempotent) creates:
- Doctors `1`–`5`: Dr. Wanjiku, Dr. Otieno, Dr. Mwangi, Dr. Achieng, Dr. Kamau.
- Mon–Fri 08:00–12:00 and 13:00–17:00 (16 slots/day).
- Patients `demo1@example.com` … `demo5@example.com` (IDs `1`–`5`), password
  `DemoPass123!`.

The README publishes the live URL, demo credentials, and doctor IDs.

## Submission checklist

- [ ] Live Render URL reachable over HTTPS.
- [ ] `main` deploys automatically; pipeline described in the README.
- [ ] Seeded doctors and demo patients documented.
- [ ] Tests run on every PR.
- [ ] Postgres plan has backups if the review window matters.
- [ ] `/healthz` and `/readyz` monitored.
- [ ] No secrets in git; no public Postgres access.
