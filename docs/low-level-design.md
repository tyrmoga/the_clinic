# Low-level design

Back to the [README](../README.md). The schema is in [Data model](data-model.md);
architectural rationale is in [Key decisions](decisions.md); rendered diagrams
(ERD, race sequence, state machines, validation flow) are in
[Diagrams](diagrams.md).

This document covers the internals needed to build and verify the assessment:
module layout, the shared slot logic, transaction boundaries, locking, tests,
migrations, and delivery. It describes the intended build; application code is
not in this repository yet.

## 1. Scope against the assessment

| Assessment item | Status | Where handled |
|---|---|---|
| `POST /appointments` | Required | §5 |
| `GET /doctors/{id}/availability` | Required | §8 |
| `PATCH /appointments/{id}/cancel` | Required | §6 |
| `PATCH /appointments/{id}/reschedule` | Required | §7 |
| Validation + meaningful status codes | Required | §3, §12 |
| Structured code, basic tests | Required | §2, §13 |
| Public deployment + CI/CD | Required | §15 |
| Bonus: `GET /patients/{id}/appointments` | Implemented | §9 |
| Bonus: no booking within 1 hour | Implemented | §4 |
| Patient auth | Included (seeded demo credentials) | §10 |
| Idempotency keys, rate limiting, cache | Included | §3, §5, §8 |
| Input validation | Required | §3 |
| Waitlist | Deferred; design only | [Future extensions](future-extensions.md) |

Build order: required endpoints + auth/validation → bonus endpoints → tests →
idempotency/rate limiting → deploy/CI. The deferred waitlist is not built.

## 2. Runtime and modules

| Path | Responsibility |
|---|---|
| `app/main.py` | App factory, middleware, exception handlers, `/healthz` |
| `app/api/` | Routers, auth dependency, request/response schemas |
| `app/services/` | Use cases, validation, transaction boundaries |
| `app/db/repositories/` | SQL only — no HTTP or business rules |
| `app/db/models.py` | SQLAlchemy table mappings |
| `app/core/config.py` | Settings from environment |
| `app/core/security.py` | argon2id hashing, JWT issue/verify |
| `app/core/cache.py` | Process-local `TTLCache` for availability |
| `app/core/rate_limit.py` | Token buckets (per patient/IP) |
| `app/core/errors.py` | Domain errors → error envelope |
| `migrations/` | Alembic revisions |
| `tests/` | Unit tests for slot logic/services, integration tests on Postgres |

Environment variables: `DATABASE_URL`, `JWT_SECRET`, `JWT_TTL_MINUTES`,
`APP_ENV`, `AVAILABILITY_CACHE_TTL_SECONDS`, `RATE_LIMIT_AUTH_PER_MINUTE`,
`RATE_LIMIT_BOOKING_PER_MINUTE`, `TRUSTED_HOSTS`, `FORWARDED_ALLOW_IPS`.

Rules:
- Routers never open transactions; services own them.
- Repositories never raise HTTP errors; they return rows or raise domain errors.
- Anything that needs DB time or a DB constraint runs inside the transaction.
- Middleware order: request ID → error envelope → rate limit → route auth.

## 3. Request pipeline

```
request
  ├─ request_id middleware          (X-Request-ID in, generated if absent)
  ├─ error-envelope middleware      (maps domain errors to JSON)
  ├─ rate-limit middleware          (429 + Retry-After; skips /healthz, /readyz)
  ├─ auth dependency                (JWT → patient_id; 401)
  └─ router → service → repository → Postgres
```

All errors use the README envelope:
`{"error": {"code", "message", "details", "request_id"}}`. Validation failures
put per-field reasons in `details.fields`; never echo the submitted password.

### Input validation

Rendered decision order: [booking validation flow](diagrams.md#6-booking-validation-order).

Validation happens at three layers, and the API maps failures explicitly:
1. **Edge**: body-size limit (1 MB), header limits, rate limit.
2. **Schema boundary (Pydantic)**: `extra="forbid"`, strict types, bounded
   lengths; unknown fields are rejected rather than ignored.
3. **Domain/DB**: slot rules, working hours, ownership, uniqueness, DB checks.

| Input | Rule | Failure |
|---|---|---|
| Request body | JSON object, `Content-Type: application/json`, ≤ 1 MB | `415`, `413`, `400` |
| `email` | trim, lowercase, max 254, valid address | `422` |
| `password` | 8–128 chars, no null bytes | `422` |
| `full_name` | trim, 1–200 chars | `422` |
| `phone` | optional, max 32 chars, E.164-ish | `422` |
| `doctor_id` | positive integer | `422` |
| Path `{id}` | valid UUID (appointment) / positive integer (doctor, patient) | `400` |
| `start_at` | ISO-8601 with `Z` or an offset; normalized to UTC; naive values rejected | `400` (parse) / `422` (naive or out of range) |
| `date` query | `YYYY-MM-DD`, required, valid calendar date | `400` |
| `reason` | trim, 1–500 chars | `422` |
| `limit` | 1–100 (default 20) | `422` |
| `cursor` | base64url of `{start_at, id}`, valid shape | `400` |
| `Idempotency-Key` | optional, 8–128 printable ASCII | `400` |

Additional rules:
- Only parameterized SQL/ORM queries; no string interpolation of input.
- Response models are explicit allowlists; `password_hash` is never serialized.
- Ownership is checked from the token, not from a body/path `patient_id`.
- Request IDs are returned on every error; logs keep them for correlation.
- Malformed JSON is mapped from the framework default to the envelope; semantic
  validation is `422`, malformed syntax is `400`.

## 4. Shared slot logic

One function is the source of truth for both availability and booking
validation, so a slot can never be valid in one path and invalid in the other.

`generate_slots(doctor, date) -> list[timestamptz]`:
1. Read `doctor.timezone` and `slot_minutes`.
2. Map the date's weekday to `doctor_working_hours.weekday`. Python `weekday()`
   is 0=Monday; Postgres `EXTRACT(DOW)` is 0=Sunday and `EXTRACT(ISODOW)` is
   1=Monday. Normalize once at the repository boundary.
3. For each working interval, convert local wall-clock boundaries to UTC with
   `AT TIME ZONE doctor.timezone`.
4. Step by `slot_minutes`; the last slot starts at `end_time - slot_minutes`.
5. Union and dedupe intervals, then sort.

`validate_slot(doctor, start_at, db_now) -> None | SlotError`:
1. Reject `start_at` not aligned to the slot grid (anchored at the interval
   start) with `doctor.slot_minutes`.
2. Reject `start_at` not present in `generate_slots(doctor, local_date)`.
3. Reject `start_at < db_now + interval '1 hour'` (bonus rule; this also covers
   the past). Without the bonus, the predicate is `start_at <= db_now`.
4. Accept.

Booking and availability call the same functions; the DB is still the arbiter of
exact-slot occupancy via the partial unique index. If slot lengths ever vary, the
occupancy check becomes a range overlap plus a per-doctor-day advisory lock — see
[Changing slot duration](slot-duration-migration.md).

## 5. Booking — `POST /appointments`

Rendered race: [two patients for one slot](diagrams.md#2-two-patients-race-for-one-slot).

```
Client                API                    Service / DB
  │  POST + Bearer      │                         │
  ├────────────────────>│ verify JWT → patient_id │
  │                     ├────────────────────────>│ BEGIN
  │                     │                         │ SELECT now() → db_now
  │                     │                         │ SELECT doctor → 404
  │                     │                         │ idempotency lookup
  │                     │                         │   hit + same hash → stored 201
  │                     │                         │   hit + different hash → 409
  │                     │                         │ validate_slot() → 422
  │                     │                         │ INSERT appointment
  │                     │                         │   unique violation → 409
  │                     │                         │ INSERT appointment_events(created)
  │                     │                         │ INSERT idempotency_keys(response)
  │                     │<────────────────────────┤ COMMIT
  │<────────────────────┤ 201 booking             │
```

- `db_now` is selected once per transaction and reused for every time comparison;
  app-host clocks are never trusted.
- Booking request bodies never contain `patient_id`; it comes from the token.
- `end_at` is computed server-side as `start_at + doctor.slot_minutes`.
- Insert only; no `SELECT ... FOR UPDATE` on the slot. The partial unique index
  `one_active_booking_per_slot` decides the winner.
- A cancellation frees the slot automatically because the index excludes
  `status='cancelled'`.
- If slot durations become variable, the transaction also takes
  `pg_advisory_xact_lock` per doctor-day and checks range overlap before insert
  ([Changing slot duration](slot-duration-migration.md)). For the fixed 30-minute
  product, the unique index is sufficient.

Idempotency (`Idempotency-Key` header):
- Key: `(patient_id, 'POST /appointments', Idempotency-Key)`.
- Request hash: SHA-256 of canonical JSON of the normalized body
  (`json.dumps(body, sort_keys=True, separators=(',', ':'))`).
- Replay with the same hash returns the stored status and body; a different hash
  is `409`.
- On a *slot* unique violation, roll back and re-read the idempotency row before
  returning `409`; this covers the race where the winner commits between the
  loser's lookup and insert.
- If the header is absent, no key row is written.

## 6. Cancel — `PATCH /appointments/{id}/cancel`

Rendered lifecycle: [appointment state](diagrams.md#3-appointment-state).

```
BEGIN
  SELECT * FROM appointments WHERE id = :id FOR UPDATE
  missing or owner mismatch                → 404
  status <> 'booked'                       → 409
  SELECT now() → db_now
  start_at <= db_now                       → 409 appointment_started
  UPDATE status='cancelled', reason, cancelled_at
  INSERT appointment_events(cancelled)
COMMIT
```

`SELECT ... FOR UPDATE` serializes cancel against reschedule on the same
appointment. The slot needs no cleanup: availability derives from active rows,
and the partial unique index ignores cancelled rows. A started appointment is
rejected (`409`) so a past slot cannot be freed retroactively.

## 7. Reschedule — `PATCH /appointments/{id}/reschedule`

```
BEGIN
  SELECT * FROM appointments WHERE id = :id FOR UPDATE
  missing or owner mismatch                → 404
  status <> 'booked'                       → 409
  SELECT now() → db_now
  start_at <= db_now                       → 409 appointment_started
  validate_slot(new_start_at, db_now)      → 422
  if new_start_at = old_start_at:
    COMMIT; return 200 (no-op, no audit row)
  UPDATE start_at, end_at, updated_at
  INSERT appointment_events(rescheduled, old_start_at, new_start_at)
COMMIT
```

The unique index arbitrates the new slot. A collision rolls the whole transaction
back, so the old slot is never lost. Rescheduling to the same `start_at` is a
valid no-op for the owner and returns `200`: the unique index sees the same row,
so no conflict is raised. A started appointment is rejected (`409`).

## 8. Availability — `GET /doctors/{id}/availability?date=`

```
cache lookup: availability:{doctor_id}:{date}
  hit  → return
  miss → SELECT doctor → 404
         db_now = SELECT now()
         slots = generate_slots(doctor, date)
         booked = SELECT start_at, end_at FROM appointments
                  WHERE doctor_id=:id AND status='booked'
                    AND start_at < day end AND end_at > day start
         available = slots - overlapping booked ranges - slots < db_now + 1 hour
         cache for AVAILABILITY_CACHE_TTL_SECONDS (5–10 s)
         return
```

Expected query shape (one round trip is also fine):

```sql
WITH slots AS (
  SELECT gs AS start_at
  FROM doctor_working_hours wh
  CROSS JOIN LATERAL generate_series(
    ((:date + wh.start_time) AT TIME ZONE :tz),
    ((:date + wh.end_time)   AT TIME ZONE :tz) - (:slot_minutes * interval '1 minute'),
    (:slot_minutes * interval '1 minute')
  ) AS gs
  WHERE wh.doctor_id = :doctor_id AND wh.weekday = :weekday
)
SELECT s.start_at
FROM slots s
WHERE s.start_at >= :db_now + interval '1 hour'
  AND NOT EXISTS (
    SELECT 1 FROM appointments a
    WHERE a.doctor_id = :doctor_id
      AND a.status = 'booked'
      AND a.start_at < s.start_at + (:slot_minutes * interval '1 minute')
      AND a.end_at   > s.start_at
  )
ORDER BY s.start_at;
```

- `date` is required and interpreted in the doctor's timezone; missing/invalid →
  `400`.
- Cache key includes doctor and date. A booking/cancel/reschedule invalidates the
  local key for each affected `(doctor_id, date)` — both the old and new date for a
  reschedule. Other processes serve at most 10 s of staleness, which is safe
  because booking revalidates.
- The overlap predicate works unchanged if slot lengths vary later; see
  [Changing slot duration](slot-duration-migration.md).

## 9. Bonus — `GET /patients/{id}/appointments`

Keyset pagination over `(start_at, id)`:

```sql
SELECT id, doctor_id, start_at, status
FROM appointments
WHERE patient_id = :id
  AND status = 'booked'
  AND start_at >= now()
  AND (:cursor_start IS NULL OR (start_at, id) > (:cursor_start, :cursor_id))
ORDER BY start_at, id
LIMIT :limit + 1;
```

- `limit` default 20, cap 100; fetch `limit + 1` to set `has_more`.
- Cursor is base64url of `{start_at, id}`; malformed cursor → `400`.
- `{id}` must equal the token's `patient_id`, otherwise `404`.

## 10. Auth

- **Register** — normalize email, enforce password ≥ 8 chars, argon2id hash,
  insert; duplicate email → `409`.
- **Login** — look up by email, verify the hash (verify against a dummy hash when
  the email is unknown), issue a short-lived JWT with `sub = patient_id`;
  failure → `401`.
- Protected routes derive `patient_id` from the token. Owner mismatch is `404`,
  not `403`, to avoid leaking existence.
- Auth is not required by the assessment, so the README publishes seeded demo
  credentials (`demo1@clinic.test` … `demo5@clinic.test`, password
  `DemoPass123!`) and doctor IDs `1`–`5`, so an evaluator can exercise the
  required endpoints immediately.

## 11. Deferred: waitlist

Rendered lifecycle: [waitlist entry state](diagrams.md#4-waitlist-entry-state-deferred).

Not built. The full design — endpoints, schema, FIFO offer flow, savepoint-based
confirm, and tests — is in [Future extensions](future-extensions.md). Nothing in
this build creates waitlist tables or routes.

The reason is scope, not correctness: the waitlist adds a second state machine
and races that are not required by the assessment, and the four required
endpoints plus delivery matter more.

## 12. Locking and error mapping

| Flow | Isolation | Explicit locks | Arbitration |
|---|---|---|---|
| Book | READ COMMITTED | none | `one_active_booking_per_slot` |
| Cancel | READ COMMITTED | appointment `FOR UPDATE` | row lock |
| Reschedule | READ COMMITTED | appointment `FOR UPDATE` | row lock + slot index |
| Idempotent replay | READ COMMITTED | none | `idempotency_keys` unique index |

`READ COMMITTED` is sufficient because every race is decided by a unique index or
a row lock; `SERIALIZABLE` would add retry handling for no correctness gain. If
slot durations vary, booking adds `pg_advisory_xact_lock` per doctor-day and a
range overlap check ([Changing slot duration](slot-duration-migration.md)).

| Internal condition | Response |
|---|---|
| Missing/invalid JWT | `401` |
| Malformed JSON / bad date / bad cursor / bad idempotency key | `400` |
| Body over 1 MB | `413` |
| Wrong `Content-Type` | `415` |
| Unknown doctor/appointment/patient, or non-owner | `404` |
| Slot unique violation | `409 slot_taken` |
| Already cancelled | `409` |
| Appointment already started | `409 appointment_started` |
| Field validation (length, type, unknown field) | `422` with `details.fields` |
| Outside working hours, off-grid, past, or within 1 hour | `422` |
| Idempotency key reused with a different body | `409` |
| Rate limit exceeded | `429` + `Retry-After` |
| DB unavailable / pool exhausted | `503` + `Retry-After` |

## 13. Tests

Unit (no DB):
- Slot generation: normal day, lunch split, single-interval day, 30-minute
  alignment, weekday mapping, timezone conversion.
- `validate_slot`: off-grid, outside hours, past, exactly 1 hour from now (edge),
  and valid.
- Cursor encoding/decoding.

Integration (real Postgres, migrations applied):
- Book: `201`; duplicate → `409`; outside hours → `422`; past/within 1 hour →
  `422`; unknown field → `422`; malformed body → `400`; wrong content type → `415`.
- Availability: excludes booked, cancelled slots reappear, excludes slots within
  1 hour, respects date/timezone, overlap predicate.
- Cancel: frees the slot; second cancel → `409`; already started → `409`;
  unknown → `404`.
- Reschedule: old slot free, new slot taken; cancelled → `409`; started → `409`;
  same `start_at` → `200` no-op; new-slot collision → `409` and old slot
  unchanged.
- Concurrency: two parallel bookings of the same slot using separate sessions
  and a barrier → exactly one `201`, one `409`, one active row.
- Bonus: patient listing order, keyset pagination, non-owner `404`.
- Validation: password length, email shape, bad cursor, oversized body, unknown
  fields, naive `start_at`.
- Idempotency: replay returns the stored `201`; same key with a different body →
  `409`.
- Rate limit: burst above the bucket → `429` with `Retry-After`.

CI runs the integration suite against a Postgres service container. Aim for
coverage of the booking service and all four required endpoints.

## 14. Migrations and seed data

- `alembic upgrade head` is the only schema path; no `create_all()` in production.
- Seed command creates:
  - Doctors `1`–`5`: Dr. Wanjiku, Dr. Otieno, Dr. Mwangi, Dr. Achieng, Dr. Kamau.
  - Mon–Fri 08:00–12:00 and 13:00–17:00 working hours (16 slots/day).
  - Patients `demo1@clinic.test` … `demo5@clinic.test`, password
    `DemoPass123!` (demo environment only).
- Seed is idempotent (`ON CONFLICT DO NOTHING` or a lookup by natural key) so it
  can run on every deploy.
- The evaluator needs known doctor IDs for availability and booking; publish
  them in the README, along with the demo credentials.

## 15. Local run, CI/CD, and deployment

Local:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # DATABASE_URL, JWT_SECRET
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload
```

OpenAPI is available at `/docs`; this is the quickest way for an evaluator to
exercise the API.

CI (GitHub Actions, branch `main`):
1. On every PR: install deps, run `ruff check`, start a Postgres service, run
   `alembic upgrade head`, run `pytest -q`, and check the app imports.
2. On merge to `main`: run the same tests, then deploy through Render
   auto-deploy (branch protection requires the PR checks) or call a Render deploy
   hook from the workflow after tests pass.
3. Migrations: a paid web service uses the Render pre-deploy command
   (`alembic upgrade head`); on the free tier, the workflow runs the migration
   against the Postgres external URL before deploying. Migrations are
   backward-compatible for one release (expand/contract), so a failed deploy does
   not break the running version.
4. If the health check fails, Render keeps the previous deploy; roll back in the
   dashboard.

Deployment notes:
- Render terminates TLS and provides the public URL; Postgres is on Render's
  private network and is not publicly reachable.
- `/healthz` checks the process; `/readyz` checks `SELECT 1`.
- Paid Render Postgres provides managed backups/PITR; the free database expires
  after 30 days and has no backups.
- The README must list the public URL, the deploy branch (`main`), a one-line
  pipeline description, the demo credentials, and doctor IDs.
- Full deployment details: [Deployment (Render)](deployment.md).

## 16. Operational details

- Structured logs include `request_id`, method, path, status, duration; never log
  passwords, tokens, or request bodies containing them.
- `/healthz` for liveness; `/readyz` for DB connectivity.
- Daily cleanup deletes `idempotency_keys` older than 24 h.
- Backups follow the Render Postgres plan: paid plans provide managed backups and
  PITR; the free plan has none ([Deployment](deployment.md#backups-and-recovery)).
- Alerts: 5xx rate, p99 latency, failed deploy, database storage.
- Postgres is not publicly reachable; Render manages the host and TLS.
