"""Bootstrap must answer truthfully and never report an assumed live feed."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_db, None)


def test_bootstrap_with_an_empty_store_is_not_ready(client):
    response = client.get("/api/v1/market/bootstrap")
    assert response.status_code == 200

    body = response.json()
    assert body["can_trade"] is False
    assert body["status"] == "INITIALIZING"
    assert body["websocket"]["connected"] is False
    assert body["websocket"]["data_fresh"] is False
    assert body["data_quality"]["series_complete"] == 0


def test_bootstrap_reports_blockers_per_instrument(client):
    body = client.get("/api/v1/market/bootstrap").json()
    readiness = next(iter(body["readiness"].values()))

    assert readiness["can_trade"] is False
    assert readiness["blockers"]
    assert readiness["flags"]["websocket_connected"] is False


def test_verify_endpoint_lists_every_configured_series(client):
    body = client.get("/api/v1/market/verify").json()
    assert body["series"]
    for row in body["series"]:
        assert row["sync_status"] == "REQUESTED"
        assert row["candle_count"] == 0


def test_sync_requires_authentication(client):
    assert client.post("/api/v1/market/sync").status_code == 401
