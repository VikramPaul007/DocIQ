from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_reports_db_and_storage_ok(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["db"] is True
    assert body["storage"] is True
    assert body["status"] == "ok"
