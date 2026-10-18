# Deployment (Render)

Back to the [README](../README.md). This is the target deployment for the
assessment: Render web service + Render Postgres, with GitHub Actions running
tests, migrations, and the deploy on merge to `main`.

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

GitHub Actions ── tests on PR ── migrations + seed + deploy hook on main ──> Render
```

Render manages the host, TLS certificates, and the network. The app owns
validation, auth, rate limiting, and migrations.

## Render resources

| Resource | Setting |
|---|---|
| Web service | Python runtime, build `pip install -r requirements.txt`, start `uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips="*"` |
| Health check | `/readyz` |
| Postgres | Internal connection URL; paid plan recommended |
| Environment | Set in the Render dashboard, never in git |
| Auto-deploy | Off; GitHub Actions triggers the deploy hook after tests and migrations |
| Python | `.python-version` pins 3.12 |
| Pre-deploy | `alembic upgrade head` — optional on paid web services |

Environment variables: `DATABASE_URL`, `JWT_SECRET`, `JWT_TTL_MINUTES`,
`APP_ENV`, `AVAILABILITY_CACHE_TTL_SECONDS`, `RATE_LIMIT_ENABLED`,
`RATE_LIMIT_AUTH_PER_MINUTE`, `RATE_LIMIT_AVAILABILITY_PER_MINUTE`,
`RATE_LIMIT_BOOKING_PER_MINUTE`, `RATE_LIMIT_DEFAULT_PER_MINUTE`,
`MAX_BODY_BYTES`, `LOG_LEVEL`, `TRUSTED_HOSTS`, `CORS_ORIGINS`.

`DATABASE_URL` comes from the Render Postgres internal URL. `JWT_SECRET` is a
generated value; rotate it by deploying a new secret and accepting the old token
window if needed. The Blueprint sets `TRUSTED_HOSTS` to
`clinic-api-4b70.onrender.com`. The **external** URL (used from CI for
migrations/seed) requires TLS; enable it for your driver (e.g.
`sslmode=require` for libpq, `ssl=require` for asyncpg).

## Blueprint

`render.yaml` defines the web service and database. Apply it as a Render
Blueprint, then set these GitHub repository secrets:

- `RENDER_DATABASE_URL` — the Postgres **external** URL, with TLS enabled.
- `RENDER_DEPLOY_HOOK_URL` — the web service's deploy hook.

The Blueprint keeps `autoDeploy: false`: the CI deploy job runs migrations, seeds
demo data, and only then triggers the hook. If you prefer Render's native
auto-deploy, enable it in the dashboard and remove the hook step, but ensure
migrations run before the new version receives traffic.

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
  instances. The GitHub Actions deploy job runs the migration against the
  Postgres **external** URL before triggering the deploy. Do not run migrations
  at app startup with multiple instances.
- Migrations must be backward-compatible for one release (expand/contract), so a
  failed deploy does not leave the old version broken.
- The deploy job also runs the idempotent seed (`python -m app.seed`) against the
  external URL, so demo doctors and patients exist on every deploy.
- A daily GitHub Actions workflow (`.github/workflows/cleanup.yml`) runs
  `python -m app.cleanup` to prune idempotency keys older than 24 hours.
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
2. Run `alembic upgrade head` against the Postgres **external** URL
   (`RENDER_DATABASE_URL`), then run `python -m app.seed`.
3. Trigger the Render deploy hook (`RENDER_DEPLOY_HOOK_URL`).
4. Render keeps the previous deploy for rollback if the health check fails.
5. The deploy job fails if either secret is missing. After the hook, it polls
   `/readyz` and a demo login until the live service answers, so a broken deploy
   fails CI.

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
- `/healthz` for liveness; `/readyz` checks the database and that migrations have run.

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
- [ ] `render.yaml` applied and `RENDER_DATABASE_URL` / `RENDER_DEPLOY_HOOK_URL`
      secrets configured.
- [ ] `main` deploys automatically; pipeline described in the README.
- [ ] Seeded doctors and demo patients documented.
- [ ] Tests run on every PR.
- [ ] Postgres plan has backups if the review window matters.
- [ ] `/healthz` and `/readyz` monitored.
- [ ] No secrets in git; no public Postgres access.
