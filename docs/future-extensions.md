# Future extensions

Back to the [README](../README.md). These are deliberately **not** in the current
build. They are kept here so the design work is not lost and the trigger for
building each one is explicit. The waitlist state machine is rendered in
[Diagrams](diagrams.md#4-waitlist-entry-state-deferred).

## Waitlist (deferred)

**Why deferred:** the assessment requires four endpoints, the bonus endpoints,
tests, deployment, and CI/CD. A waitlist adds a second state machine (waiting /
offered / booked / expired), lazy expiry, FIFO locking, and offer-versus-walk-up
races. It is a product answer to demand exceeding supply, not a correctness
requirement. It should be built only after the required API is hardened and
deployed.

**Trigger to build:** supply is consistently exhausted (most days have no free
slots) and patients ask for a way to be notified of cancellations.

**Design:** keep the design below; do not create these tables or endpoints until
the trigger is met.

### Endpoints

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

- `POST /waitlist` — duplicate active entry for `(doctor, patient, date)` →
  `409`. Joining other dates is allowed (one active entry per date).
- `GET /waitlist` — reading an entry whose `offer_expires_at` has passed lazily
  expires it; the queue advances on the next cancel.
- `POST /waitlist/{id}/confirm` — books exactly like `POST /appointments`
  (same validation, same unique index, same `Idempotency-Key` support) → `201`.
  `422` if the offer expired, `409` if the slot was taken meanwhile (offers do
  not hold slots), `404` non-owner. On success the entry → `booked`; on a lost
  `409` it returns to `waiting` for the next freed slot that day.
- `DELETE /waitlist/{id}` — leave the queue → `204`.

### Schema

```sql
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
-- at most one active row per patient per doctor-day
CREATE UNIQUE INDEX one_active_waitlist_entry
  ON waitlist_entries (doctor_id, patient_id, date)
  WHERE status IN ('waiting', 'offered');
-- queue scan: head of line for a freed slot on a given day
CREATE INDEX waitlist_queue
  ON waitlist_entries (doctor_id, date, created_at)
  WHERE status = 'waiting';
-- patient's own entries and live offers
CREATE INDEX waitlist_patient
  ON waitlist_entries (patient_id, created_at);
```

### Flow

Offer on a freed slot, inside the cancel/reschedule transaction:

```sql
UPDATE waitlist_entries
   SET status='expired'
 WHERE doctor_id=:d AND date=:day AND status='offered'
   AND offer_expires_at < now();

SELECT id FROM waitlist_entries
 WHERE doctor_id=:d AND date=:day AND status='waiting'
 ORDER BY created_at, id
 FOR UPDATE SKIP LOCKED
 LIMIT 1;
```

The entry is marked `offered` with `offer_expires_at = now() + 15 minutes`. An
offer is a right of first refusal, **not a hold**: availability does not exclude
offered slots.

Confirm uses the booking sequence inside a savepoint so a lost slot race returns
the entry to `waiting`:

```sql
BEGIN;
  SELECT * FROM waitlist_entries WHERE id=:id FOR UPDATE;
  -- owner, status='offered', not expired checks
  SAVEPOINT book_slot;
  INSERT INTO appointments ...;
  -- slot unique violation:
  --   ROLLBACK TO SAVEPOINT book_slot;
  --   UPDATE waitlist_entries SET status='waiting';
  --   COMMIT; return 409
  INSERT INTO appointment_events ...;
  UPDATE waitlist_entries SET status='booked';
COMMIT;
```

The savepoint is required because a failed `INSERT` aborts the whole Postgres
transaction; rolling back to it keeps the entry-row lock so the entry can return
to `waiting` atomically with the `409`.

### Tests when built

- Duplicate active join → `409`.
- FIFO offer order; `SKIP LOCKED` with two simultaneous cancels.
- Expiry on read, and expiry before assigning a new offer.
- Confirm racing a walk-up booking → one success, one `409`, entry back to
  `waiting`.
- Confirm after expiry → `422`.
- Leave → `204`; non-owner → `404`.
