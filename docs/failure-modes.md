# Failure modes & degradation

Back to the [README](../README.md). Demand assumptions are in
[Scale estimates](../README.md#2-scale-estimates).

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
| Abuse (scripted booking spam) | Wasted slots | Rate-limit at the edge (token bucket, e.g. 30/min/patient) → `429` + `Retry-After`. Limits throttle *speed*, not *how many* slots one patient holds (no per-patient cap in this build) | Legit users unaffected |
| Connection exhaustion under a spike | Every query slows | Bounded async pool (10–20/instance); pooler (PgBouncer) when instances scale | Degrades gracefully |
| **Pandemic demand surge** (whole town ≈ 100k patients registered, 5 doctors) | ~2,000 booking attempts/day for 80 slots; ~1,944 fail; reads up ~20× vs today | Writes stay supply-capped; failed attempts return fast `409`/`waitlist`, rate limiting protects the reads; cache availability | "No slots available — join the waitlist / see next available date" |
| **Flash crowd at slot release** (10k refresh in 60 s) | ~167 read QPS; unmanaged client retries could multiply it 2–3× | Rate limit + `429 Retry-After`, 5–10 s availability cache, client backoff+jitter and a retry budget | Slots load a little slower; some users see "retry shortly" instead of timeouts |
| Waitlist offer expires unconfirmed (15 min) | Entry stays stale until read | Lazy expiry on `GET /waitlist`; the slot was never held, so it was openly bookable anyway; queue advances on the next cancel | "Offer expired — you're back in the queue" |
| Waitlist confirm races a walk-up booking | Unique index arbitrates the slot | Confirm gets `409 slot_taken`; the entry returns to `waiting` for the next freed slot that day | Deterministic; no double-booking |
| Same `Idempotency-Key` replayed with a different body | `request_hash` mismatch on the stored row | `409` (client bug); the original stored response is untouched | Safe retry; misuse is visible |
| Brute-force password guessing | Repeated `POST /auth/login` floods auth | Tighter per-IP bucket at the edge → `429 Retry-After`; argon2id keeps a single hash slow | Legit users unaffected |
