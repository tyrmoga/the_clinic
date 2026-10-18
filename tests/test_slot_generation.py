from datetime import UTC, date, datetime, time, timedelta

from app.services.availability import (
    Availability,
    Slot,
    filter_future_slots,
    generate_slots,
)


def test_generate_slots_splits_lunch_and_converts_to_utc() -> None:
    slots = generate_slots(
        day=date(2026, 10, 19),
        timezone="Africa/Nairobi",
        slot_minutes=30,
        intervals=[(time(8, 0), time(12, 0)), (time(13, 0), time(17, 0))],
    )

    assert len(slots) == 16
    assert slots[0].start_at == datetime(2026, 10, 19, 5, 0, tzinfo=UTC)
    assert slots[7].start_at == datetime(2026, 10, 19, 8, 30, tzinfo=UTC)
    assert slots[8].start_at == datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
    assert slots[-1].start_at == datetime(2026, 10, 19, 13, 30, tzinfo=UTC)
    assert slots[-1].end_at == datetime(2026, 10, 19, 14, 0, tzinfo=UTC)
    assert all(slot.end_at - slot.start_at == timedelta(minutes=30) for slot in slots)


def test_generate_slots_returns_nothing_for_no_intervals() -> None:
    assert generate_slots(date(2026, 10, 19), "Africa/Nairobi", 30, []) == []


def test_cached_slots_inside_the_hour_are_removed() -> None:
    now = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)
    availability = Availability(
        doctor_id=1,
        day=now.date(),
        timezone="Africa/Nairobi",
        slot_minutes=30,
        slots=(
            Slot(now + timedelta(minutes=30), now + timedelta(minutes=60)),
            Slot(now + timedelta(hours=2), now + timedelta(hours=2, minutes=30)),
        ),
    )

    filtered = filter_future_slots(availability, now)

    assert [slot.start_at for slot in filtered.slots] == [now + timedelta(hours=2)]
