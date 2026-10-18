# Data model

Back to the [README](../README.md). The deferred waitlist schema is in
[Future extensions](future-extensions.md); changing the slot length is covered in
[Changing slot duration](slot-duration-migration.md). A rendered ERD is in
[Diagrams](diagrams.md#1-erd-implemented-schema).

Store: **PostgreSQL (relational)** — joins, referential integrity, and (decisively)
a database-enforced uniqueness constraint. NoSQL buys nothing here.

```sql
CREATE TABLE doctors (
  id           BIGSERIAL PRIMARY KEY,
  full_name    TEXT NOT NULL,
  timezone     TEXT NOT NULL DEFAULT 'Africa/Nairobi',
  slot_minutes SMALLINT NOT NULL DEFAULT 30
               CONSTRAINT slot_minutes_is_30 CHECK (slot_minutes = 30)
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
  CONSTRAINT end_at_is_30_min CHECK (end_at = start_at + interval '30 minutes'),
  CONSTRAINT cancelled_fields CHECK (
    status <> 'cancelled'
    OR (cancellation_reason IS NOT NULL AND cancelled_at IS NOT NULL)
  ),
  CONSTRAINT cancellation_reason_length CHECK (
    cancellation_reason IS NULL OR char_length(cancellation_reason) BETWEEN 1 AND 500
  )
);

-- THE invariant: at most one *active* booking per doctor per slot.
-- Cancelled rows are excluded, so a cancelled slot is immediately bookable again.
CREATE UNIQUE INDEX one_active_booking_per_slot
  ON appointments (doctor_id, start_at) WHERE status = 'booked';

-- access paths
-- the partial unique index above already serves (doctor_id, start_at) lookups
CREATE INDEX appointments_patient_upcoming
  ON appointments (patient_id, start_at) WHERE status = 'booked';

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

-- The waitlist tables are intentionally absent. They live in
-- docs/future-extensions.md and are not part of this build.

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
4. Exclude slots that collide with an active (`status='booked'`) appointment and
   that start less than 1 hour from now (the bonus rule, which also subsumes slots
   that have already started). Collision is a **range overlap** check
   (`appointment.start_at < slot_end AND appointment.end_at > slot_start`); with a
   clinic-wide 30-minute grid this is equivalent to comparing `start_at`, and it
   remains correct if slot lengths change later
   ([Changing slot duration](slot-duration-migration.md)).
