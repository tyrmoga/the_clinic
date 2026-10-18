# Changing slot duration later (zero downtime, 100M rows)

Back to the [README](../README.md). Current design: 30-minute slots, enforced by
a check constraint. This document explains how to support other durations without
taking the API offline, even with ~100M appointment rows.

## What has to change

| Today | Later |
|---|---|
| `doctors.slot_minutes` fixed at 30 by `CHECK` | `slot_minutes` allowed to vary (e.g. 15/20/30/60) |
| `CHECK (end_at = start_at + interval '30 minutes')` | `CHECK (end_at > start_at)` plus application validation against the doctor's duration |
| Availability excludes appointments with the same `start_at` | Availability excludes any **overlapping** range |
| Unique index `(doctor_id, start_at)` is the only booking arbiter | Unique index remains for exact-start races; overlap needs a range check |

`appointments.end_at` already stores the real end time, so **no data backfill is
needed** to know historical durations: `end_at - start_at` is the duration.

## Why the unique index is not enough

`one_active_booking_per_slot` prevents two bookings with the same `start_at`. If
durations vary, a 60-minute appointment at 09:00 and a 30-minute appointment at
09:30 are different start values but overlap. The overlap must be checked
separately.

At this scale (≤80 slots/day, <1 write QPS), the simplest correct approach is a
per-doctor-day advisory lock plus an overlap query in the booking transaction.
The unique index remains as the exact-start backstop. A database exclusion
constraint can replace the advisory lock later if needed.

## Migration phases (expand → migrate → contract)

### Phase 0 — preconditions

- Deploy the application code that:
  - computes `end_at` from the doctor's `slot_minutes`;
  - checks overlap with `tstzrange(start_at, end_at, '[)')`;
  - takes `pg_advisory_xact_lock` for the target `(doctor_id, local_date)` before
    the overlap check and insert;
  - keeps the feature flag off, so all new rows are still 30 minutes.
- Use `SET lock_timeout = '2s'` on every DDL connection and retry on timeout.
- Have a rollback plan that does **not** re-add the 30-minute check.

### Phase 1 — add the new invariant as `NOT VALID`

```sql
ALTER TABLE appointments
  ADD CONSTRAINT end_after_start CHECK (end_at > start_at) NOT VALID;
```

`NOT VALID` is a brief `ACCESS EXCLUSIVE` lock but does not scan the table. Then:

```sql
ALTER TABLE appointments VALIDATE CONSTRAINT end_after_start;
```

`VALIDATE CONSTRAINT` takes `SHARE UPDATE EXCLUSIVE`: reads and writes continue.
On 100M rows it may run for a while and consume I/O; run it in a low-traffic
window and monitor. Existing rows already satisfy it.

### Phase 2 — remove the hard 30-minute constraint

```sql
ALTER TABLE appointments
  DROP CONSTRAINT end_at_is_30_min;
```

The drop itself is metadata-only, but it needs a brief `ACCESS EXCLUSIVE` lock;
`lock_timeout` plus a retry avoids queuing behind a long transaction. Do not
combine this with a table rewrite.

Relax the doctors check in the same window (the table is tiny):

```sql
ALTER TABLE doctors DROP CONSTRAINT slot_minutes_is_30;
ALTER TABLE doctors
  ADD CONSTRAINT slot_minutes_positive CHECK (slot_minutes > 0);
```

The application still writes 30 for every doctor while the feature flag is off.

### Phase 3 — turn on variable durations

- Set `doctors.slot_minutes` for a pilot doctor only.
- Flip the feature flag for that doctor (or globally).
- New bookings use the configured duration; historical rows keep their stored
  `end_at` and remain valid.
- Availability switches to the overlap predicate:

```sql
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

The existing `(doctor_id, start_at)` index supports
`doctor_id = ? AND start_at < slot_end`, so this stays cheap per doctor-day.

### Phase 4 — booking concurrency

Inside one transaction:

```sql
SELECT pg_advisory_xact_lock(hashtextextended(:doctor_id || ':' || :local_date, 0));
-- overlap query against tstzrange(start_at, end_at, '[)')
INSERT INTO appointments ...;
```

- The advisory lock serializes bookings for one doctor-day across **all**
  instances and workers; it is released automatically at commit/rollback.
- At the design load this is negligible: ~80 slots/day per clinic, <1 write QPS.
- The partial unique index still catches an exact-start race if the lock is
  bypassed.
- Reschedule takes the same lock for the new doctor-day and revalidates the new
  range before updating `start_at`/`end_at`.

### Phase 5 (optional) — database-enforced range exclusion

If durations become arbitrary or the advisory lock is no longer acceptable:

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE INDEX CONCURRENTLY appointments_doctor_range
  ON appointments USING gist (doctor_id, tstzrange(start_at, end_at))
  WHERE status = 'booked';
```

Adding the `EXCLUDE` constraint itself needs a table lock/build, so schedule it
in a maintenance window or on a partitioned replacement table. Until then, the
advisory lock + unique index give the same guarantee for this write volume.

## Rollback

- Turn the feature flag off; doctors revert to 30-minute slots.
- Do **not** re-add `CHECK (end_at = start_at + 30 minutes)` if any variable-length
  rows exist — it will fail validation. The rollback is forward: keep
  `end_after_start`, keep writing validated durations.
- The application reads `end_at` rather than assuming 30, so old rows remain
  readable throughout.

## Why this is zero downtime

- No column is added and no row is rewritten.
- `VALIDATE CONSTRAINT` allows reads and writes.
- The only blocking steps are brief catalog operations guarded by `lock_timeout`.
  Strictly, they take a short `ACCESS EXCLUSIVE` lock; with `lock_timeout` plus
  retry the write pause is sub-second, and there is no table rewrite.
- Application deploy order is expand (new code, old behavior) → database change →
  enable (feature flag). At no point do old and new code disagree about stored
  data, because `end_at` was always the source of truth.
