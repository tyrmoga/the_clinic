# Design: Clinic Appointment Booking System

Savannah Informatics backend take-home assessment — **Section 1 (System Design)**.

> A small clinic (5 doctors) needs patients to book 30-minute appointment slots
> online: see which slots are free for a doctor on a day, book one so nobody else
> can take it, cancel, and reschedule. We're starting small but want to grow.


## 1. Problem & scope

### Functional requirements (Explicit)
1. **View availability** — list the free 30-minute slots for a doctor on a given
   date, computed from the doctor's working hours and existing bookings.
2. **Book a slot** — create an appointment on a free slot. The slot must fall
   within working hours, not be in the past, and not already be taken.
3. **Cancel an appointment** — mark it cancelled with a reason; the slot becomes
   bookable again. Error if already cancelled.
4. **Reschedule an appointment** — move it to a new slot; the old slot frees, the
   new one is validated exactly like a fresh booking. Error if cancelled.
5. **No booking within 1 hour of now:** the "no booking within 1 hour of now" rule is
   a required validator (`start_at >= now() + 1 hour`, else `422`).
6. *Bonus:* `GET /patients/{id}/appointments` — upcoming appointments sorted by
   date (keyset shape in §3). Notifications stay out (§9 #6), so waitlist
   discovery is in-app only.

### Functional requirements (Implied)
1. **Patient auth** — self-serve email/password register and login; `patient_id`
   comes from the bearer token, never the request body; touching someone else's
   appointment returns `404` (§6 #8).
   
2. **Waitlist** — a patient joins the waitlist for a `(doctor_id, date)`; when a
   slot frees (cancel), it is offered FIFO to the head of that day's queue and
   the patient confirms in-app before the offer expires (15 min). No SMS/email —
   offers surface via `GET /waitlist`. An offer is a right of first
   refusal, **not a hold**: the slot stays openly bookable and a lost race
   returns `409` (holds remain out of scope, §6 #11).

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
  excluding cold starts — a *target to verify by load test*, not a claim.
  Patient-perceived latency also includes network distance from Nairobi to the
  chosen Render region; free-tier cold starts are reported separately.
- **Availability:** 99.5% is a reasonable target for a small clinic (managed DB +
  1–2 always-on app instances on a paid Render tier). Not 99.99% — that costs
  redundancy we don't need. The free tier (idle instance sleeps, time-limited
  database) is a demo deployment and does not meet this target.
- **Consistency:** **strong** for the booking invariant — two patients must never
  hold the same slot. This is the one hard requirement; everything else is cheap.
- **Durability:** no lost bookings. Managed Postgres with daily backups + PITR;
  RPO ~5 min, RTO < 1 hr — targets that depend on the Render Postgres plan;
  confirm against Render's current plan terms before relying on them.
- **Security:** PII (patient name/contact) behind self-serve email/password auth:
  argon2id password hashes, short-lived bearer tokens, `patient_id` derived from
  the token, `404` for non-owner access (§6 #8). TLS at the edge.

  ### Out of scope (explicit)
- Payments, SMS/email reminders, push notifications (waitlist offers are
  discovered in-app via `GET /waitlist`).
- Doctor/admin UI for editing working hours (validated at the model level only).
- Multi-clinic tenancy, multi-region, microservices, external queue.
- Recurring appointments, slot **holds**, buffer times between slots.
- `completed` / `no_show` terminal states (only `booked` / `cancelled`, plus the
  waitlist states in §5).


  ### Assumptions
- **One clinic, one timezone** (`Africa/Nairobi`, UTC+3). Timezone is stored per
  doctor so multi-location later is a data change, not a code rewrite.
- Slot length is **30 minutes clinic-wide**, enforced as a
  constraint; the schema keeps it as a column so it can vary later.
- Working hours are a **recurring weekly schedule** (e.g. Mon–Fri 08:00–17:00,
  possibly two intervals to model a lunch break). One-off exceptions (holidays,
  time off) are **out of scope** (§9 Q3); a `doctor_time_off` table is
  the natural extension in case this is needed.
- `patient_id` comes from the authenticated principal (self-serve email/password,
  §6 #8); no unauthenticated write exists.
- Booking window: bookable from **1 hour before the slot starts** up to its start
  moment — one core validator predicate (`start_at >= now() + 1 hour`, else
  `422`), evaluated against DB `now()` inside the transaction.This means DB time is
  the single source of truth for the booking window, not patient time which can be
  modified by the patient.
- Waitlist offers expire unconfirmed after **15 minutes** and pass to the next
  entry (lazy expiry on read, FIFO per `(doctor_id, date)`). Chosen default, not
  derived — adjustable.



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
| Availability reads | ~500/day | ~50,000/day → peak **< 10 QPS** |
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
| **Successful bookings/day** | **56** | **56** | **56** (capped by number of doctors) |
| Failed attempts/day (409/422) | 44 | 944 | 1,944 |
| Read QPS (avg) | 0.006 | 0.06 | 0.12 |
| Peak-hour read QPS (with burst) | 0.14 | 1.4 | **2.8** |
| Peak-hour write-attempt QPS | 0.03 | 0.28 | **0.56** |
| `patients` table | ~1 MB | ~10 MB | ~20 MB |

What the numbers show:

1. **Patient growth cannot create write load.** At whole-town demand the clinic
   still has 80 slots/day, so 1,944 of 2,000 attempts are rejected by a fast `409`.
   The write path is bounded by supply, not demand. Cancellation/reschedule churn
   under pandemic uncertainty might be 3–5× the booking rate — still < 1 write QPS.

2. **The surge risk is reads and retries, not bookings.** The one scenario that
   stresses infrastructure is a **flash crowd**: 10,000 users (a tenth of the town) refreshing the
   moment slots open. If they all arrive within 60 s, that is ~167 read QPS; with
   one or two client retries it becomes ~300–500 QPS. Still inside one Postgres
   node's ~1,000 QPS rule of thumb, but enough to saturate a small connection pool
   and produce timeouts if the retries are unmanaged.

**What the numbers mean:** 
1. *Nothing distributed.* Even whole-town demand is
   ~3 peak read QPS and ~0.6 write-attempt QPS under 
   the smooth peak model — and ~167 read QPS even in 
   the flash-crowd worst case — still below the ~1,000 QPS
   single-node rule of thumb (a rule of thumb, not a measurement of this system). 
   No sharding, queue, or microservices. But the demand
   axis does force three **surge defenses** that the supply-only estimate would miss:

    - **Rate limiting** at the edge (per patient/IP, token bucket) plus `429` +
        `Retry-After`, so a flash crowd can't convert into a retry storm.
    - **Short-TTL availability caching** per `(doctor_id, date)` (5–10 s), because
        stale availability is safe: booking always revalidates against the DB and a
        taken slot returns `409`. Not Redis. The scale is too small to justify another
        potential point of failure.. Rather we use in-process cache that holds the computed 
        availability list for 5–10 seconds. Something like Python’s cachetools.TTLCache, or 
        a dict with timestamps, would do. In practice, what this means is:

          1. *Each instance has its own cache.* With two Render instances, or several uvicorn workers in one instance, each keeps a separate copy. A slot booked through instance A can still show as free from instance B’s cache for up to 10 seconds
          2. *There is no shared invalidation.* The entry just expires. You could also clear that doctor/day entry locally on a booking, cancel or reschedule, but that only helps the instance that handled the write.
          3. *Staleness is safe.* Booking always rechecks the database and the unique index decides, so a stale “free” slot produces a 409, never a double booking. Because the 1-hour rule is also checked at booking time, a stale list can’t let someone book a slot that has since become too soon.
          4. *It’s a good fit at this scale*. At one or two instances the cost is small.

    - **Retry backoff + jitter with a retry budget** in clients, so a 167 QPS burst
        does not double or triple itself.

A **waitlist** is the product answer to demand exceeding supply and was thus included as implied **in
scope** (§3, §5): a failed attempt converts into a queue join instead of a
dead-end `409`, and each cancel fires a FIFO offer at that day's queue. A
pandemic can also shrink supply (doctors ill), lowering the 80-slot cap —
the waitlist (§3) plus clear "next available" messaging absorbs the product
strain. The architecture does not change under a pandemic —
the *load mix and failure story* do. The only hard invariant remains
no-double-booking.



## 3. API (entry points)

REST over HTTPS/JSON; paths are exactly as the assessment specifies, with no `/v1`
prefix (versioning can be added when a breaking change arrives). All timestamps are ISO-8601 UTC
(`Z`) in responses; requests may carry an offset (e.g. `+03:00`) and are
normalized. A stable error envelope is used everywhere. Every endpoint except
`POST /auth/*` requires `Authorization: Bearer <token>`; a missing/invalid
token is `401`.

```
4xx/5xx
{ "error": { "code": "slot_taken", "message": "That slot has just been booked.",
             "details": { "start_at": "2026-10-20T06:00:00Z" }, "request_id": "req_…" } }
```

| Code | Meaning |
|---|---|
| 400 | Malformed JSON / invalid date |
| 401 | Missing / expired / invalid auth token |
| 404 | Unknown doctor / patient / appointment (also used for non-owner, to avoid leaking existence) |
| 409 | Conflict — slot taken, appointment already cancelled, offer expired, duplicate waitlist entry |
| 422 | Semantically invalid — outside working hours, less than 1 hour from now (covers past), misaligned to the grid |
| 429 | Rate limited (`Retry-After` set) |
| 500 / 503 | Server / DB unavailable (`Retry-After` set on 503) |

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

### `POST /appointments` — book
```json
// request (patient_id comes from the token, never the body)
{ "doctor_id": 3, "start_at": "2026-10-20T09:00:00+03:00" }

// 201 Created
{ "id": "1f0c…", "doctor_id": 3, "patient_id": 42,
  "start_at": "2026-10-20T06:00:00Z", "end_at": "2026-10-20T06:30:00Z",
  "status": "booked", "created_at": "2026-10-19T07:12:00Z" }
```
Validation order → error: unknown doctor `404`; not on a 30-min boundary / not
within working hours `422`; less than 1 hour from now (this also covers past
slots) `422`; slot already booked `409`. `end_at` is always `start_at + 30 min`.
Accepts an `Idempotency-Key` header (see *Retry safety* below).

### `GET /doctors/{id}/availability?date=2026-10-20`
```json
// 200 OK
{ "doctor_id": 3, "date": "2026-10-20", "timezone": "Africa/Nairobi",
  "slot_minutes": 30,
  "slots": [ { "start_at": "2026-10-20T06:00:00Z", "end_at": "2026-10-20T06:30:00Z" }, … ] }
```
Slots are **derived**, not stored (see §6 #1) and responses may be served from
the 5–10 s in-process availability cache (§6 #9). `date` is required and
interpreted in the doctor's timezone; a missing/invalid `date` is `400`.

### `PATCH /appointments/{id}/cancel`
```json
// request
{ "reason": "Patient unwell" }
// 200 OK
{ "id": "…", "status": "cancelled", "cancelled_at": "2026-10-19T07:15:00Z",
  "cancellation_reason": "Patient unwell" }
```
`reason` required (1–500 chars) → `422` if missing; `404` unknown; `409` if
already cancelled. I deally, input validation should catch this, but forcing it here is a good idea.

### `PATCH /appointments/{id}/reschedule`
```json
// request
{ "start_at": "2026-10-21T10:30:00+03:00" }
// 200 OK → updated appointment (new start_at/end_at)
```
Validates the new slot like a fresh booking; frees the old slot. `404` unknown;
`409` if the appointment is cancelled or the new slot is taken.

### Waitlist
```json
// POST /waitlist — join
{ "doctor_id": 3, "date": "2026-10-20" }
// 201 Created
{ "id": "…", "doctor_id": 3, "date": "2026-10-20", "status": "waiting", "position": 4 }

// GET /waitlist — entries + live offers
{ "data": [ { "id": "…", "doctor_id": 3, "date": "2026-10-20", "status": "offered",
    "offered_start_at": "2026-10-20T09:00:00Z",
    "offer_expires_at": "2026-10-19T07:30:00Z" } ], "has_more": false }
```
- `POST /waitlist` — duplicate active entry for `(doctor, patient, date)`
  → `409`. Joining other dates is allowed (one active entry per date).
- `GET /waitlist` — reading an entry whose `offer_expires_at` has passed
  lazily expires it; the queue advances on the *next* cancel.
- `POST /waitlist/{id}/confirm` — books exactly like `POST /appointments`
  (same validation, same unique index, same `Idempotency-Key` support) → `201`.
  `422` if the offer expired, `409` if the slot was taken meanwhile (offers do
  not hold slots), `404` non-owner. On success the entry → `booked`; on a lost
  `409` it returns to `waiting` for the next freed slot that day.
- `DELETE /waitlist/{id}` — leave the queue → `204`.

### Bonus: `GET /patients/{id}/appointments?limit=20&cursor=…`
```json
{ "data": [ { "id": "…", "doctor_id": 3, "start_at": "…", "status": "booked" } ],
  "next_cursor": "eyJzdGFydF9hdCI6…", "has_more": true }
```
Upcoming (`start_at >= now()`), `status = booked`, sorted by `start_at` then `id`;
**cursor (keyset)** pagination over `(start_at, id)`. `limit` capped at 100,
default 20. `{id}` must equal the authenticated patient's id, otherwise `404`
(the non-owner rule of §6 #8).

### Retry safety
`POST /appointments` and `POST /waitlist/{id}/confirm` are the non-idempotent
writes. The slot-uniqueness constraint already guarantees a duplicate booking
can't happen — a retry after a lost response returns `409 slot_taken`, which the
client should treat as "already booked, refresh". The **`Idempotency-Key` header
is in the minimal build**: stored per `(patient_id, endpoint, key)` for 24 h in
`idempotency_keys` (§5); a replay returns the stored `201` body verbatim, the
same key with a different request body → `409`, and rows are pruned after 24 h.
`PATCH` cancel/reschedule are state-transition based; a repeated cancel correctly
returns `409`. Waitlist join is naturally idempotent via its active-entry unique
index (second join → `409`).
 

## 4. High-level design

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
  (§6 #10) run in the app's middleware, or in a CDN/WAF placed in front — confirm
  against Render's current features.
- **FastAPI (stateless)** — all endpoints, validation, and bearer-token
  verification; also holds the process-local 5–10 s availability cache (§6 #9).
  Horizontal scale = add instances, so there's no session state to lose (each
  instance keeps its own cache window).
- **PostgreSQL** — the single source of truth; the **partial unique index** is
  what makes "no double-booking" correct under concurrency, not app logic. The
  waitlist and 24 h idempotency store are plain tables here — no queue, no Redis.
- **Alembic migrations** — schema changes are versioned and applied as a deploy
  step (needed by CI/CD in Section 3).


## 5. Data model

Store: **PostgreSQL (relational)** — joins, referential integrity, and (decisively)
a database-enforced uniqueness constraint. NoSQL buys nothing here.

```sql
CREATE TABLE doctors (
  id           BIGSERIAL PRIMARY KEY,
  full_name    TEXT NOT NULL,
  timezone     TEXT NOT NULL DEFAULT 'Africa/Nairobi',
  slot_minutes SMALLINT NOT NULL DEFAULT 30 CHECK (slot_minutes = 30)
);

CREATE TABLE doctor_working_hours (
  id        BIGSERIAL PRIMARY KEY,
  doctor_id BIGINT NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,
  weekday   SMALLINT NOT NULL CHECK (weekday BETWEEN 0 AND 6), -- 0 = Monday
  start_time TIME NOT NULL,   -- local wall-clock
  end_time   TIME NOT NULL,
  CHECK (end_time > start_time),
  UNIQUE (doctor_id, weekday, start_time)   -- two intervals (lunch) allowed
);

CREATE TABLE patients (
  id            BIGSERIAL PRIMARY KEY,
  full_name     TEXT NOT NULL,
  email         TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,           -- argon2id
  phone         TEXT
);

CREATE TYPE appointment_status AS ENUM ('booked', 'cancelled');

CREATE TABLE appointments (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  doctor_id           BIGINT NOT NULL REFERENCES doctors(id),
  patient_id          BIGINT NOT NULL REFERENCES patients(id),
  start_at            TIMESTAMPTZ NOT NULL,
  end_at              TIMESTAMPTZ NOT NULL,
  status              appointment_status NOT NULL DEFAULT 'booked',
  cancellation_reason TEXT,
  cancelled_at        TIMESTAMPTZ,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (end_at = start_at + interval '30 minutes'),
  CHECK (status <> 'cancelled' OR (cancellation_reason IS NOT NULL AND cancelled_at IS NOT NULL))
);

-- THE invariant: at most one *active* booking per doctor per slot.
-- Cancelled rows are excluded, so a cancelled slot is immediately bookable again.
CREATE UNIQUE INDEX one_active_booking_per_slot
  ON appointments (doctor_id, start_at) WHERE status = 'booked';

-- access paths
CREATE INDEX appointments_patient_upcoming
  ON appointments (patient_id, start_at) WHERE status = 'booked';
CREATE INDEX appointments_doctor_day
  ON appointments (doctor_id, start_at) WHERE status = 'booked';

-- audit trail for reschedules (cheap, keeps the unique index simple)
CREATE TABLE appointment_events (
  id              BIGSERIAL PRIMARY KEY,
  appointment_id  UUID NOT NULL REFERENCES appointments(id),
  event_type      TEXT NOT NULL CHECK (event_type IN ('created','cancelled','rescheduled')),
  old_start_at    TIMESTAMPTZ,
  new_start_at    TIMESTAMPTZ,
  reason          TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- waitlist: FIFO per (doctor_id, date); an offer is a right of first refusal,
-- not a hold — offered slots stay in availability until confirmed.
CREATE TABLE waitlist_entries (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  doctor_id        BIGINT NOT NULL REFERENCES doctors(id),
  patient_id       BIGINT NOT NULL REFERENCES patients(id),
  date             DATE NOT NULL,
  status           TEXT NOT NULL DEFAULT 'waiting'
                   CHECK (status IN ('waiting','offered','booked','cancelled','expired')),
  offered_start_at TIMESTAMPTZ,
  offer_expires_at TIMESTAMPTZ,          -- offered_at + 15 min; NULL unless 'offered'
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- at most one *active* waitlist row per patient per doctor-day
-- (re-joining is allowed after the old row is cancelled/expired/booked)
CREATE UNIQUE INDEX one_active_waitlist_entry
  ON waitlist_entries (doctor_id, patient_id, date)
  WHERE status IN ('waiting', 'offered');
-- queue scan: head of line for a freed slot on a given day
CREATE INDEX waitlist_queue
  ON waitlist_entries (doctor_id, date, created_at)
  WHERE status = 'waiting';

-- Idempotency-Key store: 24 h TTL, pruned by a daily cleanup statement
CREATE TABLE idempotency_keys (
  id              BIGSERIAL PRIMARY KEY,
  patient_id      BIGINT NOT NULL REFERENCES patients(id),
  endpoint        TEXT NOT NULL,         -- e.g. 'POST /appointments'
  key             TEXT NOT NULL,
  request_hash    TEXT NOT NULL,         -- sha256 of the canonical body
  response_status SMALLINT NOT NULL,
  response_body   JSONB NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (patient_id, endpoint, key)
);
CREATE INDEX idempotency_keys_created ON idempotency_keys (created_at);
```

**Keys and access patterns:**
- Booking/availability: look up by `(doctor_id, start_at)` — served by the unique
  index; availability reads `doctor_working_hours` by `(doctor_id, weekday)`.
- Waitlist: queue scan on `(doctor_id, date, created_at)` partial index; confirm
  hits the same `(doctor_id, start_at)` unique index as a fresh booking.
- Idempotency: unique lookup on `(patient_id, endpoint, key)`.
- Patient listing (bonus): `(patient_id, start_at)` index, keyset cursor on
  `(start_at, id)`.
- **No shard key** — one node; if multi-clinic arrives, `clinic_id` joins every
  key and the unique index becomes `(clinic_id, doctor_id, start_at)`.

**Availability algorithm** (derived, per request):
1. Load the doctor's timezone and `slot_minutes`.
2. Compute the local day range `[00:00, 24:00)` for `date`, convert to UTC via the
   zoneinfo database.
3. For the day's `weekday`, take each `(start_time, end_time)` interval and emit
   slots every `slot_minutes` (e.g. `generate_series`).
4. Exclude slots that collide with an active (`status='booked'`) appointment,
   that have already started, and that start less than 1 hour from now (core).
   Do **not** exclude waitlist-offered slots — offers don't hold (§6 #11).

## 6. Key decisions & trade-offs

| # | Decision | Solves | Worsens | Change it when |
|---|---|---|---|---|
| 1 | **Derive availability** from working hours minus bookings, don't pre-materialize slot rows | No slot-generation job or stale rows; schedule edits apply to the future automatically; single source of truth | Recomputes per request (trivial at ~16 slots/doctor/day); no place to hang per-slot metadata | Slots need their own *slot-level* state (holds, per-slot offers) → materialize |
| 2 | **Enforce no-double-booking with a DB partial unique index**, not app check-then-insert or a Redis lock | Correct under *any* concurrency (the DB arbitrates); atomic; no extra infra | Postgres-specific partial index; a collision surfaces as an exception that must be mapped to `409` | Data is sharded across nodes → the invariant must live in the shard key (`clinic_id, doctor_id, start_at`) |
| 3 | **Single relational DB, no microservices / NoSQL** | ACID + joins + unique constraints; simplest ops; far under one node's ceiling | Single-node write ceiling (~1k QPS) and single-region SPOF | Sustained DB CPU/connection saturation or multi-region latency requirements |
| 4 | **Store `TIMESTAMPTZ` in UTC**, generate slots in the doctor's local zone | Correct across timezones and DST; unambiguous sort/comparison | Localization logic in the read path | Never — this is table stakes for scheduling |
| 5 | **Reschedule = in-place `UPDATE start_at`** inside a transaction (not insert-new + cancel-old) | One active row; the unique index still arbitrates the new slot; atomic | Loses the "old slot" unless recorded → `appointment_events` audit table | Full bitemporal history is required → separate immutable appointment + slot-assignment tables |
| 6 | **Synchronous booking, no queue** | Immediate confirmation; simplest; no async failure surface | A slow/peak write blocks the caller (irrelevant at <1 QPS) | Write spikes or expensive post-booking work (notifications) → queue *the notifications*, never the booking |
| 7 | **"In the past" validated against `now()` inside the transaction** | Immune to app-host clock skew; the DB is the single clock | `now()` can't be a CHECK constraint (non-immutable) → enforced in app within the txn | Multi-region where a global clock matters → use a single time authority |
| 8 | **Self-serve email/password auth; `patient_id` from a short-lived bearer token** | Closes the spoofing hole: nobody books/cancels for another patient (owner mismatch → `404`); login/register get their own rate-limit bucket | Password storage, reset, and token issuance are now ours; no OTP flows (SMS out of scope); JWT secret management joins the ops surface | Staff SSO/OAuth arrives, or passwordless becomes a hard requirement |
| 9 | **In-process availability cache (5–10 s TTL per `(doctor_id, date)`) in the minimal build** | Cuts a flash-crowd read burst by ~90% with zero extra infra; stale data is safe because booking revalidates and a taken slot returns `409` | Each instance serves up to 10 s of staleness independently (no cross-instance invalidation); a synchronized flash crowd can stampede the same key | Instances exceed ~2–3 (federation → shared Redis/TTL), or staleness fails product acceptance |
| 10 | **Rate-limit booking/availability at the edge** (app middleware, or a CDN/WAF in front) | Keeps a retry storm and scripted spam away from Postgres | Legitimate bursty users can be throttled; limits need tuning; in-app counters are per instance (no shared state) | Instances exceed ~2–3 → shared limiter store, or edge/CDN rate limiting |
| 11 | **DB-backed waitlist with lazy FIFO offers; offers do not hold slots** | Demand > supply gets a product answer with zero queue/notification infra; partial unique index keeps one active entry per patient-doctor-day; cancel → offer is a single transaction | In-app discovery only (no push); an unheld offer can be lost to a walk-up booking (`409` on confirm); expiry checked lazily on read | Product needs guaranteed holds (add hold state + availability exclusion) or SMS push (queue the notification, §6 #6) |
| 12 | **`Idempotency-Key` store (24 h) in the minimal build** | Retries after a lost `201` return the original response instead of a confusing `409`; one scheme covers booking and waitlist-confirm | One small table + a 24 h prune job; clients must send and reuse the header | Clients stay offline longer than 24 h → extend TTL; key volume grows → Redis with native TTL |

**The design's breaking point:** it is *correct* up to a single Postgres node.
That's the one place where a hard ceiling exists, and it's ~1,000 QPS. The write
path is supply-capped, so it is never the first pressure; the first pressure is a
**flash-crowd read burst plus retries** (§2b), which a rate limit and a short-TTL
availability cache absorb without changing the architecture. The design fails when
the product becomes multi-clinic/multi-region, not when it gets more doctors or
more patients.

## 7. Failure modes & degradation

| Failure | What happens | Degradation / recovery | User experience |
|---|---|---|---|
| Two patients book the same slot simultaneously | Unique index accepts one `INSERT`, rejects the other | Loser gets `409 slot_taken` + a hint to refresh availability | One confirms, one sees "just taken, pick another" |
| Client retries after a lost `201` | Second `INSERT` violates the unique index | `409` (or stored `201` with `Idempotency-Key`) | No double-booking; UI treats `409` as "already booked" |
| Cancel vs reschedule race on the *same* appointment | `SELECT … FOR UPDATE` serializes them | Second txn sees the updated `status` and returns `409` | Deterministic; no lost update |
| Two reschedules target the *same* new slot | Unique index arbitrates | One succeeds, one `409` | Clear conflict, no overlap |
| **Postgres down** | All writes/reads fail | API returns `503` + `Retry-After`; no partial commits (transactions roll back); managed PITR restore | "Service unavailable, try shortly" — no lost data |
| **App instance crash mid-booking** | Transaction rolls back | Stateless app; managed platform restarts; no orphan state | A retry rebooks cleanly |
| **API instance down (single instance)** | Outage until restart | Run 2 instances behind the LB (stateless) | Brief blip; sessions are stateless |
| Schedule edited after bookings exist | Existing appointments stay `booked` | Availability recomputes from new hours minus existing bookings; conflict list is a follow-up admin feature | Booked patients keep their slot |
| Working-hours overlap/misconfigured | Two intervals produce duplicate slots | Union intervals + dedupe in the generator; `UNIQUE(doctor_id, weekday, start_time)` prevents duplicate rows | No phantom slots |
| Clock skew across hosts | "Past" check wrong | Validate against DB `now()` in the txn; NTP on hosts | Consistent cutoff |
| Abuse (scripted booking spam) | Wasted slots | Rate-limit at the edge (token bucket, e.g. 30/min/patient) → `429` + `Retry-After`. Limits throttle *speed*, not *how many* slots one patient holds (no per-patient cap — see §9) | Legit users unaffected |
| Connection exhaustion under a spike | Every query slows | Bounded async pool (10–20/instance); pooler (PgBouncer) when instances scale | Degrades gracefully |
| **Pandemic demand surge** (whole town ≈ 100k patients registered, 5 doctors) | ~2,000 booking attempts/day for 80 slots; ~1,944 fail; reads up ~20× vs today | Writes stay supply-capped; failed attempts return fast `409`/`waitlist`, rate limiting protects the reads; cache availability | "No slots available — join the waitlist / see next available date" |
| **Flash crowd at slot release** (10k refresh in 60 s) | ~167 read QPS; unmanaged client retries could multiply it 2–3× | Rate limit + `429 Retry-After`, 5–10 s availability cache, client backoff+jitter and a retry budget | Slots load a little slower; some users see "retry shortly" instead of timeouts |
| Waitlist offer expires unconfirmed (15 min) | Entry stays stale until read | Lazy expiry on `GET /waitlist`; the slot was never held, so it was openly bookable anyway; queue advances on the next cancel | "Offer expired — you're back in the queue" |
| Waitlist confirm races a walk-up booking | Unique index arbitrates the slot | Confirm gets `409 slot_taken`; the entry returns to `waiting` for the next freed slot that day | Deterministic; no double-booking |
| Same `Idempotency-Key` replayed with a different body | `request_hash` mismatch on the stored row | `409` (client bug); the original stored response is untouched | Safe retry; misuse is visible |
| Brute-force password guessing | Repeated `POST /auth/login` floods auth | Tighter per-IP bucket at the edge → `429 Retry-After`; argon2id keeps a single hash slow | Legit users unaffected |

## 8. Scale evolution

**Current bottleneck:** none expected from the §2 estimates (not yet measured). On the **supply** axis the single
Postgres node is the eventual ceiling, but the write path is supply-capped and
tiny. On the **demand** axis the first pressure is the availability **read** path
during a flash crowd — and an edge rate limit plus a 5–10 s cache absorbs it long
before the DB is the problem.

- **Patient demand 10× (pandemic, same 5 doctors):** no schema change. The rate
  limit and the 5–10 s availability cache are already on by default; failed
  attempts are cheap `409`s the client converts into waitlist joins, and each
  cancel recycles a slot through the FIFO offer. Successful bookings stay at
  80/day.
- **Supply 10× (50 doctors):** no change. Optionally add a pooler and 2 app
  instances.
- **Supply 100× (500 doctors):** still one Postgres node comfortably. Add
  `clinic_id` to all tables and scope the unique index to
  `(clinic_id, doctor_id, start_at)` *if* growth means multiple clinics. Partition
  `appointments` by month once it reaches tens of millions of rows (years away).
  Archiving for old rows.
- **1,000× (many clinics / regions):** shard by `clinic_id` (the invariant stays
  inside one shard), add read replicas for reporting, move the API behind a global
  LB. The booking write path per clinic remains small — the scaling problem is
  isolation between clinics, not volume.

**Signal to evolve:** availability-read p99 > 300 ms or connection-pool wait
time rising (demand axis); DB CPU > 70% sustained, write p99 > 300 ms, or the
`appointments` table passing ~10M rows (supply axis). No readings exist yet — the system has not been load-tested.
