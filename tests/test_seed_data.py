from collections import Counter

from app.seed import DEMO_PASSWORD, DOCTORS, PATIENTS, WORKING_HOURS


def test_seed_has_five_doctors_and_five_patients() -> None:
    assert len(DOCTORS) == 5
    assert len(PATIENTS) == 5
    assert [doctor["id"] for doctor in DOCTORS] == [1, 2, 3, 4, 5]
    assert [patient["id"] for patient in PATIENTS] == [1, 2, 3, 4, 5]


def test_seed_has_two_intervals_per_weekday() -> None:
    assert len(WORKING_HOURS) == 50
    per_doctor = Counter(entry["doctor_id"] for entry in WORKING_HOURS)
    assert set(per_doctor.values()) == {10}

    for entry in WORKING_HOURS:
        assert entry["weekday"] in range(5)
        assert entry["start_time"] < entry["end_time"]


def test_demo_credentials_are_documented() -> None:
    emails = {patient["email"] for patient in PATIENTS}
    assert emails == {f"demo{number}@clinic.test" for number in range(1, 6)}
    assert len(DEMO_PASSWORD) >= 8
