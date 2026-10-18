# Clinic Appointment Booking System

| Submission | |
|---|---|
| Live API | `https://clinic-api-4b70.onrender.com` |
| API docs | `https://clinic-api-4b70.onrender.com/docs` · `https://clinic-api-4b70.onrender.com/openapi.json` |
| Repository | this repo, branch `main` |
| Demo login | `demo1@example.com` / `DemoPass123!` (login returns the patient id) |
| Doctors | IDs `1`–`5` |
| AI reflection | [docs/ai-reflection.md](docs/ai-reflection.md) |


> A small clinic (5 doctors) needs patients to book 30-minute appointment slots
> online: see which slots are free for a doctor on a day, book one so nobody else
> can take it, cancel, and reschedule. We're starting small but want to grow.

## Evaluator quick start

All required endpoints are in the interactive OpenAPI docs. Click **Authorize**
and paste the bearer token from the login call. The script below requires `curl`
and `jq`.

```bash
BASE_URL=https://clinic-api-4b70.onrender.com
DATE=$(python3 -c 'import datetime as d; from zoneinfo import ZoneInfo; t=d.datetime.now(ZoneInfo("Africa/Nairobi")).date(); print(t + d.timedelta(days=(7 - t.weekday()) % 7 or 7))')
TOKEN=$(curl -s "$BASE_URL/auth/login" -H 'Content-Type: application/json' \
  -d '{"email":"demo1@example.com","password":"DemoPass123!"}' | jq -r .access_token)

# 1. availability
curl -s "$BASE_URL/doctors/1/availability?date=$DATE" \
  -H "Authorization: Bearer $TOKEN"

# 2. book (start_at is canonical; start_time is accepted as an alias)
APPT=$(curl -s -X POST "$BASE_URL/appointments" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"doctor_id\":1,\"start_at\":\"${DATE}T09:00:00+03:00\"}" \
  | jq -r .id)

# 3. reschedule
curl -s -X PATCH "$BASE_URL/appointments/$APPT/reschedule" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"start_at\":\"${DATE}T10:00:00+03:00\"}"

# 4. cancel
curl -s -X PATCH "$BASE_URL/appointments/$APPT/cancel" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"reason":"demo"}'
```

The bearer token identifies the patient. `patient_id` in the **booking** body is
optional and is only a compatibility assertion: if supplied it must match the
token (otherwise `404`); if omitted, the token's patient is used. `start_time` is
accepted as an alias for `start_at` in request bodies. `GET /` redirects to
`/docs`.

The design sections follow; the submission run steps and CI/CD note are at the end.

## 1. Problem & scope

### Functional requirements (required by the assessment)
1. **View availability** — list the free 30-minute slots for a doctor on a given
   date, computed from the doctor's working hours and existing bookings.
2. **Book a slot** — create an appointment on a free slot. The slot must fall
   within working hours, not be in the past, and not already be taken.
3. **Cancel an appointment** — mark it cancelled with a reason; the slot becomes
   bookable again. Error if already cancelled.
4. **Reschedule an appointment** — move it to a new slot; the old slot frees, the
   new one is validated exactly like a fresh booking. Error if cancelled.

### Bonus features (implemented)
1. *Bonus:* `GET /patients/{id}/appointments` — upcoming appointments sorted by
   date (keyset shape in §3).
2. *Bonus:* **No booking within 1 hour of now** — implemented as a validator
   (`start_at >= now() + 1 hour`, else `422`); this also covers past slots.

### Additional features (beyond the assessment)
1. **Patient auth** — self-serve email/password register and login; `patient_id`
   comes from the bearer token; an optional body `patient_id` on booking must
   match it; touching someone else's appointment returns `404`
   (see [decision #8](docs/decisions.md)).
   
2. **Waitlist (deferred)** — demand above supply would benefit from FIFO offers,
   but it is not required by the assessment and adds a second state machine plus
   races. The design is retained in [Future extensions](docs/future-extensions.md);
   it is not part of this build.

### Non-functional requirements
- **Scale (now):** 5 doctors × ~16 slots/day ≈ 80 bookable slots/day; assume ~70%
  utilization → ~56 bookings/day. **Supply growth horizon:** ~100× (500 doctors)
  is still a rounding error for one database. **Demand growth** is modeled
  separately in §2b.
- **Read vs write:** availability reads dominate (patients browse several days
  before booking); writes are few. Still read-heavy by maybe 10:1, but both sides
  are tiny.
- **Demand surge:** patient demand can grow ~20× without warning (a pandemic in
  a town of ~100k people with a single clinic, i.e. the whole town registered)
  while doctor supply stays fixed. Successful writes remain
  **supply-capped** (80 slots/day); the surge shows up as availability reads,
  failed booking attempts, retry storms, and cancellation churn (see §2b).
- **Latency target (unmeasured):** server-side p99 < 300 ms per endpoint,
  excluding network time — a *target to verify by load test*, not a claim.
  Patient-perceived latency also includes network distance from Nairobi to the
  chosen Render region; free-tier cold starts are reported separately.
- **Availability:** 99.5% is a reasonable target for a small clinic (managed DB +
  1–2 always-on app instances on a paid Render tier). Not 99.99% — that costs
  redundancy we don't need. The free tier (idle instance sleeps, time-limited
  database) is a demo deployment and does not meet this target.
- **Consistency:** **strong** for the booking invariant — two patients must never
  hold the same slot. This is the one hard requirement; everything else is cheap.
- **Durability:** no lost bookings. Managed Render Postgres with backups + PITR;
  RPO ~5 min, RTO < 1 hr — targets that depend on the chosen plan. The free
  database expires after 30 days and has no backups.
- **Security:** PII (patient name/contact) behind self-serve email/password auth:
  argon2id password hashes (parameters tuned for Render's 0.1 CPU / 512 MB free
  tier, hashing off the event loop), short-lived bearer tokens, `patient_id`
  derived from the token, `404` for non-owner access
  ([decision #8](docs/decisions.md)). Strict input validation, in-app rate limits
  (optional Cloudflare in front), Postgres not publicly reachable, TLS at the
  Render edge. See [Deployment (Render)](docs/deployment.md).

  ### Out of scope (explicit)
- Payments, SMS/email reminders, push notifications.
- Doctor/admin UI for editing working hours (validated at the model level only).
- Multi-clinic tenancy, multi-region, microservices, external queue.
- Recurring appointments, slot **holds**, buffer times between slots.
- `completed` / `no_show` terminal states (only `booked` / `cancelled`).
- The waitlist — designed in [Future extensions](docs/future-extensions.md).


  ### Assumptions
- **One clinic, one timezone** (`Africa/Nairobi`, UTC+3). Timezone is stored per
  doctor so multi-location later is a data change, not a code rewrite.
- Slot length is **30 minutes clinic-wide**, enforced as a
  constraint. The column exists so it can vary later; the zero-downtime path is
  in [Changing slot duration](docs/slot-duration-migration.md).
- Working hours are a **recurring weekly schedule** (e.g. Mon–Fri 08:00–17:00,
  possibly two intervals to model a lunch break). One-off exceptions (holidays,
  time off) are **out of scope**; a `doctor_time_off` table is
  the natural extension in case this is needed.
- `patient_id` comes from the authenticated principal (self-serve email/password,
  [decision #8](docs/decisions.md)); no unauthenticated write exists.
- Booking window: a slot is bookable only while `start_at >= now() + 1 hour` —
  bookings close **1 hour before** the slot starts. One core validator predicate
  (`start_at >= now() + 1 hour`, else `422`), evaluated against DB `now()` inside
  the transaction. This means DB time is
  the single source of truth for the booking window, not patient time which can be
  modified by the patient.
- Cancelling or rescheduling an appointment that has already started
  (`start_at <= now()`) is rejected (`409`) — a past slot cannot be freed or moved.



## 2. Scale estimates

Growth has **two independent axes** and they behave differently:

- **Supply growth** (more doctors) raises the number of slots and therefore the
  number of *successful* writes.
- **Demand growth** (more patients — e.g. a pandemic) does **not** raise
  successful writes; they are capped by supply. It raises availability reads,
  failed booking attempts, retry storms, and contention on the *same* few slot
  keys.


### 2a. Supply: doctor growth

| Quantity | Now (5 doctors) | 100× (500 doctors) |
|---|---|---|
| Slots/day | 5 × 16 = 80 | 8,000 |
| Successful bookings/day (~70%) | ~56 | ~5,600 |
| Write QPS (avg) | 56 / 86,400 ≈ **0.0007** | ≈ 0.065 |
| Peak write QPS (×10 burst) | < 0.01 | < 1 |
| Availability reads | ~500/day | ~50,000/day → peak **~14 QPS** |
| Storage/day | ~56 × ~400 B ≈ 22 KB | ≈ 2.2 MB |
| Storage/year | ~8 MB | ~0.8 GB |

### 2b. Demand: patient growth / pandemic surge

Scenario: a pandemic in a town of ~100,000 people served by this one clinic, so
registered patients are capped at 100,000 (the whole town); 5,000 registered is
today. Assumptions: 2% of registered patients try to book on a given day; each makes
~5 availability reads and 1 booking attempt. Peak model: 20% of daily activity
lands in the 1-hour morning opening window, with a 5× burst inside it. **Supply is
held at 5 doctors** — the worst case for contention.

| Quantity | Now | Pandemic 10× | Whole town (ceiling) |
|---|---|---|---|
| Registered patients | 5,000 | 50,000 | 100,000 |
| Daily active booking users | 100 | 1,000 | 2,000 |
| Availability reads/day | 500 | 5,000 | 10,000 |
| Booking attempts/day | 100 | 1,000 | 2,000 |
| **Successful bookings/day** | **56** | **80** | **80** (supply cap; 100% utilization) |
| Failed attempts/day (409/422) | 44 | 920 | 1,920 |
| Read QPS (avg) | 0.006 | 0.06 | 0.12 |
| Peak-hour read QPS (with burst) | 0.14 | 1.4 | **2.8** |
| Peak-hour write-attempt QPS | 0.03 | 0.28 | **0.56** |
| `patients` table | ~1 MB | ~10 MB | ~20 MB |

What the numbers show:

1. **Patient growth cannot create write load.** At whole-town demand the clinic
   still has 80 slots/day, so 1,920 of 2,000 attempts are rejected by a fast `409`.
   The write path is bounded by supply, not demand. Cancellation/reschedule churn
   under pandemic uncertainty might be 3–5× the booking rate — still < 1 write QPS.

2. **The surge risk is reads and retries, not bookings.** The one scenario that
   stresses infrastructure is a **flash crowd**: 10,000 users (a tenth of the town) refreshing the
   moment slots open. If they all arrive within 60 s, that is ~167 read QPS; with
   one or two client retries it becomes ~300–500 QPS. Still inside one Postgres
   node's ~1,000 QPS rule of thumb, but enough to saturate a small connection pool
   and produce timeouts if the retries are unmanaged.

**What the numbers mean:**

1. **Nothing distributed.** Even whole-town demand is ~3 peak read QPS and
   ~0.6 write-attempt QPS under the smooth peak model — and ~167 read QPS even in
   the flash-crowd worst case — still below the ~1,000 QPS single-node rule of
   thumb (a rule of thumb, not a measurement of this system). No sharding, queue,
   or microservices. But the demand axis does force three **surge defenses** that
   the supply-only estimate would miss:
   - **Rate limiting** in the app (per patient/IP, token bucket) plus `429` +
     `Retry-After`, so a flash crowd can't convert into a retry storm. Optional
     Cloudflare in front adds edge/WAF protection.
   - **Short-TTL availability caching** per `(doctor_id, date)` (5–10 s), because
     stale availability is safe: booking always revalidates against the DB and a
     taken slot returns `409`. Not Redis — the scale is too small to justify
     another point of failure. An in-process `TTLCache` (or a dict with
     timestamps) holds the computed list for 5–10 seconds. In practice:
     - Each process has its own cache. With two app instances or several uvicorn
       workers, each keeps a separate copy. A slot booked through worker A can
       still show as free from worker B's cache for up to 10 seconds.
     - There is no shared invalidation. The entry expires; clearing the
       doctor/day entry locally on a write only helps the process that handled it.
     - Staleness is safe. Booking rechecks the database and the unique index
       decides, so a stale "free" slot produces a `409`, never a double booking.
       The 1-hour rule is also rechecked at booking time.
     - It fits this scale: one or two app processes make the cost small.
   - **Retry backoff + jitter with a retry budget** in clients, so a 167 QPS burst
     does not double or triple itself.

A **waitlist** is the product answer to demand exceeding supply. It is
deliberately deferred — the design is in
[Future extensions](docs/future-extensions.md), but it is not part of this
build. A pandemic can also shrink supply (doctors ill), lowering the 80-slot
cap; clear "next available" messaging absorbs the product strain without another
state machine. The architecture does not change under a pandemic —
the *load mix and failure story* do. The only hard invariant remains
no-double-booking.



## 3. API (entry points)

REST over HTTPS/JSON; paths are exactly as the assessment specifies, with no `/v1`
prefix (versioning can be added when a breaking change arrives). All timestamps are ISO-8601 UTC
(`Z`) in responses; requests may carry an offset (e.g. `+03:00`) and are
normalized. A timestamp without an offset is interpreted in the doctor's
timezone. A stable error envelope is used everywhere. Every endpoint except `POST /auth/*` requires `Authorization: Bearer <token>`; a missing/invalid
token is `401`. `GET /` redirects to `/docs`; `GET /openapi.json` exposes the
schema.

```
4xx/5xx
{ "error": { "code": "slot_taken", "message": "That slot has just been booked.",
             "details": { "start_at": "2026-10-20T06:00:00Z" }, "request_id": "req_…" } }
```

| Code | Meaning |
|---|---|
| 400 | Malformed JSON / invalid date / invalid cursor |
| 401 | Missing / expired / invalid auth token |
| 404 | Unknown doctor / patient / appointment (also used for non-owner, to avoid leaking existence) |
| 409 | Conflict — slot taken, appointment already cancelled, appointment already started |
| 413 | Request body too large |
| 415 | Unsupported media type (expected `application/json`) |
| 422 | Semantically invalid — outside working hours, less than 1 hour from now (covers past), misaligned to the grid, invalid field values |
| 429 | Rate limited (`Retry-After` set) |
| 500 / 503 | Server / DB unavailable (`Retry-After` set on 503) |

Requests are validated strictly: unknown fields are rejected; `start_time` is
accepted as an alias for `start_at`, and `patient_id` is accepted optionally on
`POST /appointments` only. Strings are length-bounded, timestamps with `Z`/an
offset are normalized to UTC, naive timestamps are interpreted in the doctor's
timezone, `reason` is 1–500 chars, and pagination `limit` is 1–100. Failures
return the envelope with per-field details; malformed syntax is `400`, semantic
failures are `422`. Full rules are in the
[low-level design](docs/low-level-design.md#input-validation).

### `POST /auth/register`, `POST /auth/login` — auth
```json
// register request
{ "email": "a@example.com", "password": "…", "full_name": "Achieng O.", "phone": "+254…" }
// 201 Created / 200 OK
{ "access_token": "…", "token_type": "bearer", "patient_id": 42 }
```
Passwords stored as argon2id hashes; the token is a short-lived JWT carrying
`patient_id` for owner checks. Weak password (`< 8 chars`) → `422`; duplicate
email → `409`. `login` and `register` get their own tighter rate-limit bucket
than availability reads (brute force). Password reset is out of scope.

**Demo access:** seeded patients `demo1@example.com` … `demo5@example.com`,
password `DemoPass123!`; doctors `1`–`5`. These are demo-only credentials; see
[Deployment](docs/deployment.md#demo-data-and-access).

```bash
BASE_URL=https://clinic-api-4b70.onrender.com
DATE=$(python3 -c 'import datetime as d; from zoneinfo import ZoneInfo; t=d.datetime.now(ZoneInfo("Africa/Nairobi")).date(); print(t + d.timedelta(days=(7 - t.weekday()) % 7 or 7))')
TOKEN=$(curl -s "$BASE_URL/auth/login" -H 'Content-Type: application/json' \
  -d '{"email":"demo1@example.com","password":"DemoPass123!"}' | jq -r .access_token)

curl -s "$BASE_URL/doctors/1/availability?date=$DATE" \
  -H "Authorization: Bearer $TOKEN"
```

### `POST /appointments` — book
```json
// request (patient_id comes from the token; optional body patient_id is an assertion)
{ "doctor_id": 3, "start_at": "2026-10-20T09:00:00+03:00", "patient_id": 42 }
// "start_time" is accepted as an alias for "start_at" in request bodies

// 201 Created
{ "id": "1f0c…", "doctor_id": 3, "patient_id": 42,
  "start_at": "2026-10-20T06:00:00Z", "end_at": "2026-10-20T06:30:00Z",
  "status": "booked", "created_at": "2026-10-19T07:12:00Z" }
```
Validation order → error: unknown doctor `404`; body `patient_id` present and not
the token's patient `404`; misaligned to the 30-minute slot grid / not within
working hours `422`; less than 1 hour from now (this also covers past slots)
`422`; slot already booked `409`. `end_at` is always `start_at + 30 min`.
Accepts an `Idempotency-Key` header (see *Retry safety* below). Responses use
`start_at`; `start_time` is a request alias only.

### `GET /doctors/{id}/availability?date=2026-10-20`
```json
// 200 OK
{ "doctor_id": 3, "date": "2026-10-20", "timezone": "Africa/Nairobi",
  "slot_minutes": 30,
  "slots": [ { "start_at": "2026-10-20T06:00:00Z", "end_at": "2026-10-20T06:30:00Z" }, … ] }
```
Slots are **derived**, not stored (see [data model](docs/data-model.md)) and responses may be served from
the 5–10 s in-process availability cache ([decision #9](docs/decisions.md)). `date` is required and
interpreted in the doctor's timezone; a missing/invalid `date` is `400`.

### `PATCH /appointments/{id}/cancel`
```json
// request
{ "reason": "Patient unwell" }
// 200 OK
{ "id": "…", "status": "cancelled", "cancelled_at": "2026-10-19T07:15:00Z",
  "cancellation_reason": "Patient unwell" }
```
`reason` is required (1–500 chars) → `422` if missing; `404` unknown; `409` if
already cancelled or the appointment has already started.

### `PATCH /appointments/{id}/reschedule`
```json
// request
{ "start_at": "2026-10-21T10:30:00+03:00" }
// "start_time" is accepted as an alias
// 200 OK → updated appointment (new start_at/end_at)
```
Validates the new slot like a fresh booking; frees the old slot. `404` unknown;
`409` if the appointment is cancelled, has already started, or the new slot is
taken. Rescheduling to the **same** `start_at` returns `200` as a no-op, even
inside the 1-hour window; the no-op check runs before slot validation.

### Bonus: `GET /patients/{id}/appointments?limit=20&cursor=…`
```json
{ "data": [ { "id": "…", "doctor_id": 3, "start_at": "…", "status": "booked" } ],
  "next_cursor": "eyJzdGFydF9hdCI6…", "has_more": true }
```
Upcoming (`start_at >= now()`), `status = booked`, sorted by `start_at` then `id`;
**cursor (keyset)** pagination over `(start_at, id)`. `limit` capped at 100,
default 20. `{id}` must equal the authenticated patient's id, otherwise `404`
(the non-owner rule of [decision #8](docs/decisions.md)).

### Retry safety and rate limits
`POST /appointments` is the non-idempotent write. The slot-uniqueness constraint
already guarantees a duplicate booking can't happen — a retry after a lost
response returns `409 slot_taken`, which the client should treat as "already
booked, refresh". The **`Idempotency-Key` header is in the build**: stored per
`(patient_id, endpoint, key)` for 24 h in `idempotency_keys`
([data model](docs/data-model.md)); a replay returns the stored `201` body
verbatim, the same key with a different request body → `409`, and rows are
pruned after 24 h. `PATCH` cancel/reschedule are state-transition based; a
repeated cancel correctly returns `409`.

Every API endpoint (except the health checks) is rate-limited (token bucket per
patient/IP, tighter buckets for `/auth/*`) and returns `429` with `Retry-After`.
The app enforces rate limits and body-size limits; if Cloudflare is placed in
front, it can add edge/WAF protection.
 

## 4. Architecture at a glance

Patient → Render edge (managed TLS) → FastAPI web service (stateless, in-process
5–10 s availability cache) → Render Postgres (source of truth; a partial unique
index arbitrates booking; not publicly reachable). Alembic runs as a pre-deploy
step on paid web services; on the free web tier, migrations run from CI against
the external database URL. See [Deployment (Render)](docs/deployment.md) and the
[component diagram](docs/diagrams.md#5-component-diagram).

## 5. Documentation

- [High-level design](docs/high-level-design.md) — full diagram and component rationale
- [Low-level design](docs/low-level-design.md) — module layout, request flows, transaction boundaries, validation, and locking
- [Diagrams](docs/diagrams.md) — ERD, race sequence, state machines, component, validation flow
- [Data model](docs/data-model.md) — schema, indexes, and the availability algorithm
- [Key decisions & trade-offs](docs/decisions.md)
- [Failure modes & degradation](docs/failure-modes.md)
- [Scale evolution](docs/scale-evolution.md)
- [Deployment (Render)](docs/deployment.md) — Render resources, free-tier limits, CI/CD, backups
- [Changing slot duration](docs/slot-duration-migration.md) — zero-downtime path for 100M rows
- [Future extensions](docs/future-extensions.md) — deferred waitlist design
- [AI reflection](docs/ai-reflection.md) — Section 4 answers (to be completed)

## 6. Run locally

Requires Python 3.12+ and a local PostgreSQL.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # set DATABASE_URL and JWT_SECRET
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload
```

Run the checks with `ruff check .` and `pytest -q`.

For integration tests, set `TEST_DATABASE_URL` to a local test database whose
name ends in `_test`, then run:

```bash
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/clinic_test \
  RUN_DB_TESTS=1 pytest -q
```

Open `http://localhost:8000/docs` and log in with `demo1@example.com` /
`DemoPass123!`.

## 7. CI/CD and deployment

- Every pull request runs `ruff check` and `pytest` against a PostgreSQL service
  container. The workflow is `.github/workflows/ci.yml`.
- Merge to `main` runs the same tests, applies migrations and the idempotent
  seed against the external database URL, then triggers the Render deploy hook.
- `render.yaml` defines the web service and Postgres; `autoDeploy` is off so the
  deploy happens only after tests and migrations pass.
- Repository secrets: `RENDER_DATABASE_URL` and `RENDER_DEPLOY_HOOK_URL`. The
  deploy job fails if either is missing.
- After the hook, CI polls `/readyz` and a demo login; a broken deploy fails the
  workflow.
- A daily cleanup workflow prunes expired idempotency keys.
- Render keeps the previous deploy for rollback if the health check fails.
- Full details: [Deployment (Render)](docs/deployment.md).

## 8. Section 4: AI reflection

The four reflection questions are answered in
[docs/ai-reflection.md](docs/ai-reflection.md).
