"""Health check 엔드포인트 테스트"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_healthz_endpoint():
    """루트 readiness probe (/healthz) 테스트"""
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
