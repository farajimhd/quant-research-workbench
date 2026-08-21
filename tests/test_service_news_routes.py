from fastapi.testclient import TestClient

from src.backend.app import app


def test_news_histogram_preserves_success_payload(monkeypatch) -> None:
    expected = {"rows": [{"bucket_utc": "2026-08-21T13:00:00Z", "total_rows": 3}]}
    monkeypatch.setattr("src.backend.app.service_news_histogram", lambda: expected)

    with TestClient(app) as client:
        response = client.get("/api/services/news/histogram")

    assert response.status_code == 200
    assert response.json() == expected


def test_news_histogram_reports_clickhouse_timeout_as_unavailable(monkeypatch) -> None:
    def raise_timeout() -> dict:
        raise TimeoutError("timed out")

    monkeypatch.setattr("src.backend.app.service_news_histogram", raise_timeout)

    with TestClient(app) as client:
        response = client.get("/api/services/news/histogram")

    assert response.status_code == 503
    assert "ClickHouse dependency unavailable" in response.json()["detail"]


def test_news_today_reports_connection_failure_as_unavailable(monkeypatch) -> None:
    def raise_connection_error(limit: int, sort: str) -> dict:
        raise ConnectionError(f"unreachable for {limit} {sort}")

    monkeypatch.setattr("src.backend.app.service_news_today_rows", raise_connection_error)

    with TestClient(app) as client:
        response = client.get("/api/services/news/today?limit=25&sort=asc")

    assert response.status_code == 503
    assert "ClickHouse dependency unavailable" in response.json()["detail"]
