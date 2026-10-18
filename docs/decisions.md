# Key decisions & trade-offs

Back to the [README](../README.md). Scale context is in
[Scale estimates](../README.md#2-scale-estimates).

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
| 11 | **DB-backed waitlist with lazy FIFO offers; offers do not hold slots** | Demand > supply gets a product answer with zero queue/notification infra; partial unique index keeps one active entry per patient-doctor-day; cancel → offer is a single transaction | In-app discovery only (no push); an unheld offer can be lost to a walk-up booking (`409` on confirm); expiry checked lazily on read | Product needs guaranteed holds (add hold state + availability exclusion) or SMS push (queue the notification, decision #6) |
| 12 | **`Idempotency-Key` store (24 h) in the minimal build** | Retries after a lost `201` return the original response instead of a confusing `409`; one scheme covers booking and waitlist-confirm | One small table + a 24 h prune job; clients must send and reuse the header | Clients stay offline longer than 24 h → extend TTL; key volume grows → Redis with native TTL |

**The design's breaking point:** it is *correct* up to a single Postgres node.
That's the one place where a hard ceiling exists, and it's ~1,000 QPS. The write
path is supply-capped, so it is never the first pressure; the first pressure is a
**flash-crowd read burst plus retries** (§2b in the [README](../README.md)),
which a rate limit and a short-TTL availability cache absorb without changing the
architecture. The design fails when the product becomes multi-clinic/multi-region,
not when it gets more doctors or more patients.
