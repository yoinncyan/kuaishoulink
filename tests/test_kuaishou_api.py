from fastapi.testclient import TestClient

from backend.main import app


def test_health_and_initial_probe_status():
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}

        status = client.get("/api/probe/status")
        assert status.status_code == 200
        assert status.json()["state"] == "stopped"
        assert status.json()["probe"] is None


def test_frontend_is_served_when_built():
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "快手网络探针" in response.text

