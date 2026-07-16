from __future__ import annotations

from fastapi.testclient import TestClient

from kfxai.api import app
from kfxai.brain_api import app as brain_app


def test_dashboard_health() -> None:
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json()["trading_mode"] == "paper"
    assert response.json()["oanda_configured"] is False


def test_brain_has_no_order_capability() -> None:
    client = TestClient(brain_app)
    meta = client.get("/v1/meta").json()
    assert meta["can_place_orders"] is False
    response = client.post(
        "/v1/judge/regime",
        json={"ohlcv_by_instrument": {"USD_JPY": [["t", 1, 1, 1, 1, 10]]}},
    )
    assert response.status_code == 200
    assert response.json()["regime"] in {"risk_on", "risk_off", "neutral"}
