# Failure modes & degradation

Back to the [README](../README.md). Demand assumptions are in
[Scale estimates](../README.md#2-scale-estimates).

| Failure | What happens | Degradation / recovery | User experience |
|---|---|---|---|
| Two patients book the same slot simultaneously | Unique index accepts one `INSERT`, rejects the other | Loser gets `409 slot_taken` + a hint to refresh availability | One confirms, one sees "just taken, pick another" |
| Client retries after a lost `201` | Second `INSERT` violates the unique index | `409` (or stored `201` with `Idempotency-Key`) | No double-booking; UI treats `409` as "already booked" |
| Cancel vs reschedule race on the *same* appointment | `SELECT … FOR UPDATE` serializes them | Second txn sees the updated `status` and returns `409` | Deterministic; no lost update |
| Two reschedules target the *same* new slot | Unique index arbitrates | One succeeds, one `409` | Clear conflict, no overlap |
| **Postgres down** | All writes/reads fail | API returns `503` + `Retry-After`; no partial commits (transactions roll back); restore from managed backups/PITR on a paid plan | "Service unavailable, try shortly" — no lost data with a paid plan |
| **App instance crash mid-booking** | Transaction rolls back | Render restarts the service; no orphan state | A retry rebooks cleanly |
| **Free web service spins down** | First request after 15 minutes idle is slow | Render wakes it in roughly a minute; upgrade to a paid instance for always-on | One slow request, then normal |
| **Free Postgres expires** | 30 days after creation, then a 14-day grace period; data is deleted | Use a paid Postgres plan before the expiry window | Data loss unless upgraded in time |
| **Deploy fails its health check** | New version is unhealthy | Render keeps the previous deploy serving traffic; roll back in the dashboard | No user-visible change |
| **Render platform outage** | The app is unreachable | Check Render's status page; retry; the database remains intact | Errors until Render recovers |
| Schedule edited after bookings exist | Existing appointments stay `booked` | Availability recomputes from new hours minus existing bookings; conflict list is a follow-up admin feature | Booked patients keep their slot |
| Working-hours overlap/misconfigured | Two intervals produce duplicate slots | Union intervals + dedupe in the generator; `UNIQUE(doctor_id, weekday, start_time)` prevents duplicate rows | No phantom slots |
| Clock skew | "Past" check wrong | Validate against DB `now()` in the txn; Render manages host clocks | Consistent cutoff |
| Abuse (scripted booking spam) | Wasted slots | In-app rate limit (token bucket, e.g. 30/min/patient) → `429` + `Retry-After`; optional Cloudflare in front. Limits throttle *speed*, not *how many* slots one patient holds (no per-patient cap in this build) | Legit users unaffected |
| Connection exhaustion under a spike | Every query slows | Bounded async pool sized to the Render plan's connection limit; upgrade or add pooling when instances scale | Degrades gracefully |
| **Pandemic demand surge** (whole town ≈ 100k patients registered, 5 doctors) | ~2,000 booking attempts/day for 80 slots; ~1,920 fail; reads up ~20× vs today | Writes stay supply-capped; failed attempts return fast `409`, rate limiting protects the reads; cache availability | "No slots available — see next available date" |
| **Flash crowd at slot release** (10k refresh in 60 s) | ~167 read QPS; unmanaged client retries could multiply it 2–3× | Rate limit + `429 Retry-After`, 5–10 s availability cache, client backoff+jitter and a retry budget | Slots load a little slower; some users see "retry shortly" instead of timeouts |
| Cancel/reschedule after the appointment has started | State change refused | `409`; a past slot is neither freed nor moved | "This appointment has already started" |
| Same `Idempotency-Key` replayed with a different body | `request_hash` mismatch on the stored row | `409` (client bug); the original stored response is untouched | Safe retry; misuse is visible |
| Brute-force password guessing | Repeated `POST /auth/login` floods auth | Tighter in-app per-IP bucket → `429 Retry-After`; optional Cloudflare; argon2id keeps a single hash slow | Legit users unaffected |
