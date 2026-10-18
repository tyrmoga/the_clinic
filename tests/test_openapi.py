from fastapi.testclient import TestClient


def test_openapi_contains_required_endpoints(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200

    paths = response.json()["paths"]
    assert {
        "/auth/register",
        "/auth/login",
        "/appointments",
        "/doctors/{doctor_id}/availability",
        "/appointments/{appointment_id}/cancel",
        "/appointments/{appointment_id}/reschedule",
        "/patients/{patient_id}/appointments",
    } <= set(paths)


def test_openapi_exposes_bearer_auth_and_tags(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()

    schemes = spec["components"]["securitySchemes"]
    assert any(scheme.get("scheme") == "bearer" for scheme in schemes.values())

    tags = {tag["name"] for tag in spec["tags"]}
    assert {"auth", "availability", "appointments", "patients"} <= tags
