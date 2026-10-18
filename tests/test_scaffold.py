from fastapi.testclient import TestClient


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_redirects_to_docs(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/docs"


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["request_id"] == response.headers["x-request-id"]


def test_request_id_is_echoed_when_valid(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": "request-123"})
    assert response.status_code == 200
    assert response.headers["x-request-id"] == "request-123"


def test_request_id_is_replaced_when_invalid(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": "not valid!"})
    assert response.status_code == 200
    request_id = response.headers["x-request-id"]
    assert request_id != "not valid!"
    assert len(request_id) == 32


def test_security_headers_are_present(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "strict-transport-security" in response.headers


def test_docs_is_not_blocked_by_csp(client: TestClient) -> None:
    response = client.get("/docs")
    assert response.status_code == 200
    assert "content-security-policy" not in response.headers


def test_body_size_limit(client: TestClient) -> None:
    response = client.post("/does-not-exist", content=b"x" * 1_000_001)
    assert response.status_code == 413
    body = response.json()
    assert body["error"]["code"] == "payload_too_large"
    assert body["error"]["request_id"] == response.headers["x-request-id"]
