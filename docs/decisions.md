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
| 7 | **"In the past" validated against `now()` inside the transaction**; cancel/reschedule on a started appointment → `409` | Immune to app-host clock skew; the DB is the single clock; past slots cannot be freed or moved | `now()` can't be a CHECK constraint (non-immutable) → enforced in app within the txn | Multi-region where a global clock matters → use a single time authority |
| 8 | **Self-serve email/password auth; `patient_id` from a short-lived bearer token** | Closes the spoofing hole: nobody books/cancels for another patient (owner mismatch → `404`); login/register get their own rate-limit bucket | Password storage, reset, and token issuance are now ours; no OTP flows (SMS out of scope); JWT secret management joins the ops surface | Staff SSO/OAuth arrives, or passwordless becomes a hard requirement |
| 9 | **In-process availability cache (5–10 s TTL per `(doctor_id, date)`) in the minimal build** | Cuts a flash-crowd read burst by ~90% with zero extra infra; stale data is safe because booking revalidates and a taken slot returns `409` | Each process serves up to 10 s of staleness independently (no cross-process invalidation); a synchronized flash crowd can stampede the same key | Processes exceed ~2–3 (federation → shared Redis/TTL), or staleness fails product acceptance |
| 10 | **Rate-limit in the app** (token bucket per patient/IP); optional Cloudflare/WAF in front | Keeps a retry storm, brute-force login, and scripted spam away from Postgres; the limiter travels with the app and needs no extra service | Legitimate bursty users can be throttled; limits need tuning; in-app counters are per process (no shared state) | Many app processes → shared limiter store; or traffic needs edge/WAF protection → put Cloudflare in front |
| 11 | **Defer the waitlist** — design retained in [Future extensions](future-extensions.md) | Keeps the build focused on the required endpoints, bonus endpoints, tests, and deployment | No product answer for demand > supply yet; failed bookings are a plain `409` | Supply is consistently exhausted and waitlist demand is confirmed → build the documented extension |
| 12 | **`Idempotency-Key` store (24 h) in the minimal build** | Retries after a lost `201` return the original response instead of a confusing `409` | One small table + a 24 h prune job; clients must send and reuse the header | Clients stay offline longer than 24 h → extend TTL; key volume grows → Redis with native TTL |
| 13 | **Strict input validation at the API boundary** (Pydantic `extra="forbid"`, bounded lengths, strict date/datetime parsing) plus DB checks | Bad input fails before it reaches business logic or SQL; consistent `400`/`422` errors; no mass-assignment or unknown-field surprises | Some rules are expressed twice (app + DB); status-code mapping needs an explicit handler | A generated client/schema becomes the contract → derive validation from OpenAPI |
| 14 | **Render web service + Render Postgres** | Managed TLS, public URL, database, backups (paid), and git-based deploys; no host, firewall, or certificate work | Free web services sleep and the free Postgres expires after 30 days; less control; paid tiers cost more at scale | Traffic outgrows the plan, or a managed platform no longer fits → dedicated infrastructure |

The hard 30-minute slot is a deliberate starting point. The zero-downtime path to
variable durations (including 100M rows) is in
[Changing slot duration](slot-duration-migration.md).

**The design's breaking point:** it is *correct* up to a single Postgres node.
That's the one place where a hard ceiling exists, and it's ~1,000 QPS. The write
path is supply-capped, so it is never the first pressure; the first pressure is a
**flash-crowd read burst plus retries** (§2b in the [README](../README.md)),
which a rate limit and a short-TTL availability cache absorb without changing the
architecture. The design fails when the product becomes multi-clinic/multi-region,
not when it gets more doctors or more patients.
