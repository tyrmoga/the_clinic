from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import DataError

from app.core.errors import register_exception_handlers


def test_data_error_is_mapped_to_422() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise DataError("INSERT ...", {}, Exception("bad text"))

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"
