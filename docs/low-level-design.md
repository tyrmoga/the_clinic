# Low-level design

Back to the [README](../README.md). The schema is in [Data model](data-model.md);
architectural rationale is in [Key decisions](decisions.md).

This document covers internals: module layout, request flows, transaction
boundaries, locking, and error mapping. It describes the intended build; the
application code is not in this repository yet.

## 1. Module layout

| Path | Responsibility |
|---|---|
| `app/main.py` | App factory, middleware order, exception handlers |
| `app/api/` | Routers, auth dependency, request/response schemas |
| `app/services/` | Use cases, validation, transaction boundaries |
| `app/db/repositories/` | SQL only — no HTTP or business rules |
| `app/db/models.py` | SQLAlchemy table mappings |
| `app/core/security.py` | argon2id hashing, JWT issue/verify |
| `app/core/cache.py` | Process-local `TTLCache` for availability |
| `app/core/rate_limit.py` | Token buckets (per patient/IP) |
| `app/core/errors.py` | Domain errors → error envelope |
| `migrations/` | Alembic revisions |
| `tests/` | Unit tests for services, integration tests against Postgres |

Rules:
- Routers never open transactions; services own them.
- Repositories never raise HTTP errors; they return rows or raise domain errors.
- Validation that needs DB time or DB constraints happens inside the transaction,
  not in Pydantic.
- Middleware order: request ID → error envelope → rate limit → route auth.

## 2. Entity relationships

```
doctors 1 ──* doctor_working_hours
doctors 1 ──* appointments *──1 patients
appointments 1 ──* appointment_events
doctors 1 ──* waitlist_entries *──1 patients
patients 1 ──* idempotency_keys
```

`appointments.start_at` plus `doctor_id` is the booking key; the partial unique
index `one_active_booking_per_slot` is the only arbiter. No application-level
"check then insert".

## 3. Booking flow — `POST /appointments`

```
Client                API                    Service / DB
  │  POST + Bearer      │                         │
  ├────────────────────>│ verify JWT → patient_id │
  │                     ├────────────────────────>│ BEGIN
  │                     │                         │ SELECT now()  (DB clock)
  │                     │                         │ SELECT doctor → 404
  │                     │                         │ idempotency lookup
  │                     │                         │   hit + same hash → stored 201
  │                     │                         │   hit + different hash → 409
  │                     │                         │ validate grid/hours/1h → 422
  │                     │                         │ INSERT appointment
  │                     │                         │   unique violation → 409
  │                     │                         │ INSERT appointment_events(created)
  │                     │                         │ INSERT idempotency_keys(response)
  │                     │<────────────────────────┤ COMMIT
  │<────────────────────┤ 201 booking             │
```

Validation order inside the transaction:
1. Doctor exists and is loaded with `timezone` and `slot_minutes` → `404`.
2. `start_at` is on a 30-minute boundary and inside a working-hours interval for
   that weekday in the doctor's timezone → `422`.
3. `start_at >= now() + interval '1 hour'` using DB `now()` (also rejects the
   past) → `422`.
4. Insert. The partial unique index raises on an active duplicate → map to
   `409 slot_taken`.

Idempotency:
- Key is `(patient_id, 'POST /appointments', Idempotency-Key)`.
- Replay with the same canonical request hash returns the stored status/body;
  different hash → `409`.
- The appointment and its idempotency row commit atomically. Two concurrent
  replays serialize on the key's unique index; the loser re-reads the stored row
  and returns it.
- On a *slot* unique violation the service rolls back and performs the same
  idempotency re-read before returning `409`; this covers the race where the
  winning request commits between the loser's lookup and insert.

## 4. Cancel flow — `PATCH /appointments/{id}/cancel`

```
BEGIN
  SELECT * FROM appointments WHERE id = :id FOR UPDATE
  owner mismatch or missing                → 404
  status <> 'booked'                       → 409
  UPDATE status='cancelled', reason, cancelled_at
  INSERT appointment_events(cancelled)
  expire_stale_offers(doctor_id, date)
  offer_next_waiting(doctor_id, date)      -- see §6
COMMIT
```

The row lock serializes cancel against reschedule on the same appointment. The
freed slot needs no bookkeeping: availability is derived from active rows.

## 5. Reschedule flow — `PATCH /appointments/{id}/reschedule`

```
BEGIN
  SELECT * FROM appointments WHERE id = :id FOR UPDATE
  owner mismatch or missing                → 404
  status <> 'booked'                       → 409
  validate new slot (same rules as booking) → 422
  UPDATE start_at, end_at, updated_at
  INSERT appointment_events(rescheduled, old_start_at, new_start_at)
  offer_next_waiting(doctor_id, old_date)  -- the old slot is free again
COMMIT
```

The unique index still arbitrates the new slot. A collision rolls the whole
transaction back, so the old slot is never lost.

## 6. Waitlist internals

### Join — `POST /waitlist`

```
BEGIN
  INSERT waitlist_entries (doctor_id, patient_id, date, status='waiting')
    partial unique violation (active entry exists) → 409
COMMIT
```

Position shown to the client is the rank by `(created_at, id)` among `waiting`
entries for that doctor/date.

### Offer on a freed slot

`offer_next_waiting(doctor_id, date)` runs inside the cancel/reschedule
transaction:

```
UPDATE waitlist_entries
   SET status='expired'
 WHERE doctor_id=:d AND date=:day AND status='offered'
   AND offer_expires_at < now()

SELECT id FROM waitlist_entries
 WHERE doctor_id=:d AND date=:day AND status='waiting'
 ORDER BY created_at, id
 FOR UPDATE SKIP LOCKED
 LIMIT 1

-- if a row was returned
UPDATE waitlist_entries
   SET status='offered',
       offered_start_at=:freed_slot,
       offer_expires_at=now() + interval '15 minutes'
 WHERE id=:entry_id
COMMIT
```

An offer is a right of first refusal, not a hold: availability does not exclude
offered slots. Two simultaneous cancellations use `SKIP LOCKED` so they offer to
different queue heads rather than blocking or double-offering the same entry.

### Confirm — `POST /waitlist/{id}/confirm`

```
BEGIN
  SELECT * FROM waitlist_entries WHERE id=:id FOR UPDATE
  owner mismatch or missing                 → 404
  status <> 'offered' or now() > expires_at → 422
  SAVEPOINT book_slot
  INSERT appointment
    unique violation → ROLLBACK TO SAVEPOINT book_slot;
                       UPDATE waitlist_entries SET status='waiting';
                       COMMIT; return 409
  INSERT appointment_events(created)
  UPDATE waitlist_entries SET status='booked'
COMMIT
```

The savepoint is required because a failed `INSERT` aborts the whole Postgres
transaction; rolling back to it keeps the entry-row lock and lets the entry
return to `waiting` atomically with the `409`.

### Leave — `DELETE /waitlist/{id}`

```
UPDATE waitlist_entries SET status='cancelled'
 WHERE id=:id AND patient_id=:patient_id AND status IN ('waiting','offered')
```

Zero rows → `404` (missing or non-owner); success → `204`.

## 7. Availability flow — `GET /doctors/{id}/availability`

```
cache lookup: availability:{doctor_id}:{date}
  hit  → return
  miss → load doctor (timezone, slot_minutes) → 404
         load working hours for weekday
         load booked start_at in [local day start, day end) as UTC
         generate slots every slot_minutes
         drop booked slots, started slots, and slots < now() + 1 hour
         store in TTL cache (5–10 s)
         return
```

- Cache is per process. A write invalidates the local key for
  `(doctor_id, date)`; other instances serve at most 10 s of staleness.
- Never cache errors or empty results caused by a DB failure.
- `date` is required and interpreted in the doctor's timezone; invalid/missing →
  `400`.

## 8. Auth flows

- **Register** — normalize email, enforce password ≥ 8 chars, hash with argon2id,
  insert; duplicate email → `409`.
- **Login** — look up by email, verify hash (verify against a dummy hash when the
  email is unknown to reduce timing leakage), issue a short-lived JWT with
  `sub = patient_id`; failure → `401`.
- Every protected route resolves `patient_id` from the token. A path/body
  `patient_id` is never trusted; owner mismatch is `404`, not `403`, to avoid
  leaking existence.

## 9. Transaction and locking matrix

| Flow | Isolation | Explicit locks | Arbitration |
|---|---|---|---|
| Book | READ COMMITTED | none | `one_active_booking_per_slot` |
| Cancel | READ COMMITTED | `appointments` row `FOR UPDATE` | row lock |
| Reschedule | READ COMMITTED | `appointments` row `FOR UPDATE` | row lock + slot index |
| Waitlist join | READ COMMITTED | none | `one_active_waitlist_entry` |
| Waitlist offer | READ COMMITTED | queue head `FOR UPDATE SKIP LOCKED` | FIFO order |
| Waitlist confirm | READ COMMITTED | entry `FOR UPDATE` | row lock + slot index |
| Idempotent replay | READ COMMITTED | none | `idempotency_keys` unique index |

`READ COMMITTED` is sufficient because every race is decided by a unique index or
a row lock; `SERIALIZABLE` would add retry handling for no correctness gain.

## 10. Error mapping

| Internal condition | Response |
|---|---|
| Missing/invalid JWT | `401` |
| Malformed JSON / bad date | `400` |
| Unknown doctor/appointment/patient, or non-owner | `404` |
| Slot unique violation | `409 slot_taken` |
| Duplicate active waitlist entry | `409` |
| Already cancelled / offer expired (confirm) | `409` / `422` |
| Working-hours, grid, or 1-hour rule failure | `422` |
| Idempotency key reused with a different body | `409` |
| Rate limit exceeded | `429` + `Retry-After` |
| DB unavailable / pool exhausted | `503` + `Retry-After` |

All errors use the envelope in §3 of the README and carry the request ID.

## 11. Housekeeping

- A daily job deletes `idempotency_keys` older than 24 h (indexed on
  `created_at`).
- Waitlist expiry is lazy: stale `offered` rows are expired on read and before an
  offer is assigned. No background scheduler is required for correctness.
- Alembic runs as a deploy step before the app starts.
