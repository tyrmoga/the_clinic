# Diagrams

Back to the [README](../README.md). These are the visual companions to the
[data model](data-model.md), [low-level design](low-level-design.md), and
[high-level design](high-level-design.md). The diagrams are Mermaid; GitHub
renders them automatically. The waitlist lifecycle is included because the design
is retained in [Future extensions](future-extensions.md), even though it is
deferred.

## 1. ERD (implemented schema)

The current build has six tables. The waitlist is intentionally absent; its
deferred schema is in [Future extensions](future-extensions.md#schema).

```mermaid
erDiagram
    doctors ||--o{ doctor_working_hours : "has"
    doctors ||--o{ appointments : "booked with"
    patients ||--o{ appointments : "books"
    appointments ||--o{ appointment_events : "audits"
    patients ||--o{ idempotency_keys : "owns"

    doctors {
        bigint id PK
        text full_name
        text timezone
        smallint slot_minutes
    }

    doctor_working_hours {
        bigint id PK
        bigint doctor_id FK
        smallint weekday
        time start_time
        time end_time
    }

    patients {
        bigint id PK
        text full_name
        text email UK
        text password_hash
        text phone
    }

    appointments {
        uuid id PK
        bigint doctor_id FK
        bigint patient_id FK
        timestamptz start_at
        timestamptz end_at
        appointment_status status
        text cancellation_reason
        timestamptz cancelled_at
        timestamptz created_at
        timestamptz updated_at
    }

    appointment_events {
        bigint id PK
        uuid appointment_id FK
        text event_type
        timestamptz old_start_at
        timestamptz new_start_at
        text reason
        timestamptz created_at
    }

    idempotency_keys {
        bigint id PK
        bigint patient_id FK
        text endpoint
        text key
        text request_hash
        smallint response_status
        jsonb response_body
        timestamptz created_at
    }
```

The booking invariant lives in the partial unique index, not in the diagram:
`one_active_booking_per_slot` on `(doctor_id, start_at) WHERE status = 'booked'`.
`appointment_status` is the enum `('booked', 'cancelled')`.

## 2. Two patients race for one slot

This is the central concurrency story. Both requests validate successfully; the
database partial unique index serializes the conflicting inserts. The winner
commits a `201`; the loser sees a unique violation after the winner commits and
returns `409 slot_taken`.

```mermaid
sequenceDiagram
    autonumber
    participant A as Patient A
    participant B as Patient B
    participant API as FastAPI
    participant DB as PostgreSQL

    Note over A,B: Same doctor_id and start_at

    A->>API: POST /appointments (09:00)
    B->>API: POST /appointments (09:00)
    API->>API: verify JWT + strict validation

    par Patient A transaction
        API->>DB: BEGIN · SELECT now() · validate slot
        API->>DB: INSERT appointment
        DB-->>API: INSERT ok
        API->>DB: INSERT event + idempotency, then COMMIT
        API-->>A: 201 Created
    and Patient B transaction
        API->>DB: BEGIN · SELECT now() · validate slot
        API->>DB: INSERT appointment (blocks)
        Note over DB: partial unique index<br/>(doctor_id, start_at)<br/>WHERE status = 'booked'
        DB-->>API: unique_violation after A commits
        API->>DB: ROLLBACK, re-read idempotency
        API-->>B: 409 slot_taken
    end
```

No application-level check-then-insert is used, so there is no TOCTOU window:
the index is the arbiter even across app workers and instances.

## 3. Appointment state

The implemented states are only `booked` and `cancelled`. Reschedule is a
self-transition on `booked`. A started appointment cannot be cancelled or
rescheduled.

```mermaid
stateDiagram-v2
    [*] --> booked: POST /appointments (201)
    booked --> booked: PATCH /reschedule (200)
    booked --> cancelled: PATCH /cancel (200, reason)
    cancelled --> [*]

    note right of booked
      Reschedule to the same start_at is a valid no-op (200).
      Cancel or reschedule once started → 409.
      A started booking stays booked. There is no completed/no_show state.
    end note
```

`appointment_events` records created, cancelled, and rescheduled transitions;
a same-slot reschedule is a no-op and writes no audit row.

## 4. Waitlist entry state (deferred)

Designed in [Future extensions](future-extensions.md), not built. An offer is a
right of first refusal, not a hold: the slot stays bookable, so confirm can lose
the race and the entry returns to `waiting`.

```mermaid
stateDiagram-v2
    [*] --> waiting: POST /waitlist
    waiting --> offered: slot freed (FIFO head)
    offered --> booked: confirm wins (201)
    offered --> waiting: confirm loses race (409)
    offered --> expired: 15-minute expiry
    waiting --> cancelled: DELETE /waitlist/{id}
    offered --> cancelled: DELETE /waitlist/{id}
    booked --> [*]
    cancelled --> [*]
    expired --> [*]

    note right of offered
      Not a hold: a walk-up booking can take the slot.
      Expiry is checked lazily on read and before a new offer.
    end note
```

## 5. Component diagram

The Render deployment: managed edge and database, with the app owning validation,
rate limiting, and migrations.

```mermaid
flowchart TB
    P["Patient (web/mobile)"]

    subgraph RENDER["Render"]
        E["Render edge<br/>managed TLS · routing"]
        A["FastAPI web service<br/>uvicorn, 1 instance"]
        K["Availability cache<br/>5–10 s TTL"]
        RL["Rate limiter<br/>in process"]
        DB[("Render Postgres<br/>private network")]
        BK["Managed backups<br/>paid plan"]
        E -->|HTTPS| A
        A -->|SQL · bounded pool| DB
        A -.->|availability| K
        A --- RL
        DB -.-> BK
    end

    P -->|HTTPS/JSON + Bearer| E
    CI["GitHub Actions"] -->|tests on PR| T["pytest + Postgres"]
    CI -->|deploy on main| A
```

Render manages TLS and the public URL; Postgres is not publicly reachable. Rate
limits run in the app because Render has no built-in edge limiter. See
[Deployment](deployment.md).

## 6. Booking validation order

Every request follows this order. Any failure after `BEGIN` rolls back; no
partial writes survive.

```mermaid
flowchart TD
    S([POST /appointments]) --> AUTH{"Bearer token valid?"}
    AUTH -- no --> E401["401"]
    AUTH -- yes --> BODY{"Body valid?<br/>strict schema"}
    BODY -- no --> E4XX["400 / 413 / 415 / 422"]
    BODY -- yes --> DOC{"Doctor exists?"}
    DOC -- no --> E404["404"]
    DOC -- yes --> TXN["BEGIN · SELECT now()"]
    TXN --> IDEM{"Idempotency-Key hit?"}
    IDEM -->|same body| R201["return stored 201"]
    IDEM -->|different body| E409["409"]
    IDEM -->|none| GRID{"On the slot grid?"}
    GRID -- no --> E422["422"]
    GRID -- yes --> HOURS{"Inside working hours?"}
    HOURS -- no --> E422
    HOURS -- yes --> WINDOW{"At least 1 hour ahead?"}
    WINDOW -- no --> E422
    WINDOW -- yes --> INS["INSERT appointment"]
    INS --> UNIQ{"Slot unique index?"}
    UNIQ -- conflict --> E409S["409 slot_taken"]
    UNIQ -- ok --> EVT["insert event + idempotency"]
    EVT --> COMMIT["COMMIT"]
    COMMIT --> R201C["201 Created"]

    classDef err fill:#fee2e2,stroke:#dc2626,color:#991b1b;
    class E401,E4XX,E404,E422,E409,E409S err;
```

The 1-hour window check is the bonus rule and also rejects past slots. Without
it, the predicate is `start_at > now()`.
