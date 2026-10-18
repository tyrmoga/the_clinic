# Scale evolution

Back to the [README](../README.md). Estimates are in
[Scale estimates](../README.md#2-scale-estimates).

**Current bottleneck:** none expected from the §2 estimates (not yet measured). On the **supply** axis the single
Postgres node is the eventual ceiling, but the write path is supply-capped and
tiny. On the **demand** axis the first pressure is the availability **read** path
during a flash crowd — and an in-app rate limit plus a 5–10 s cache absorbs it long
before the DB is the problem.

- **Patient demand 10× (pandemic, same 5 doctors):** no schema change. The rate
  limit and the 5–10 s availability cache are already on by default; failed
  attempts are cheap `409`s and each cancel immediately frees the slot again.
  Successful bookings stay at 80/day. A waitlist is the product response if this
  becomes persistent, but it is deferred (see [Future extensions](future-extensions.md)).
- **Supply 10× (50 doctors):** no change. Optionally upgrade the Render instance
  and add a pooler plus a second app instance.
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
