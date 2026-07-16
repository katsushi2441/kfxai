from __future__ import annotations

from typing import Any

import pytest

from kfxai.config import Settings
from kfxai.oanda import OandaClient


class Response:
    ok = True
    status_code = 200
    headers: dict[str, str] = {}

    def __init__(self, payload: dict[str, Any]):
        self.payload = payload

    def json(self) -> dict[str, Any]:
        return self.payload


class Session:
    def __init__(self, responses: list[dict[str, Any]]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        return Response(self.responses.pop(0))


def settings(mode: str = "paper") -> Settings:
    return Settings(account_id="101-test", access_token="secret", trading_mode=mode)


def test_parses_candles_and_prices() -> None:
    session = Session([
        {"candles": [{"time": "2026-07-16T00:00:00Z", "complete": True, "volume": 7,
                       "mid": {"o": "158.1", "h": "158.3", "l": "158.0", "c": "158.2"}}]},
        {"prices": [{"instrument": "USD_JPY", "time": "2026-07-16T00:01:00Z",
                      "status": "tradeable", "bids": [{"price": "158.19"}],
                      "asks": [{"price": "158.21"}]}]},
    ])
    client = OandaClient(settings(), session=session)
    candles = client.candles("USD_JPY")
    prices = client.prices(["USD_JPY"])
    assert candles[0].close == 158.2
    assert prices["USD_JPY"].spread_pips == pytest.approx(2.0)
    assert session.calls[0]["url"].startswith("https://api-fxpractice.oanda.com/v3/")


def test_market_order_contains_server_side_protection() -> None:
    session = Session([{"orderCreateTransaction": {"id": "42"}}])
    client = OandaClient(settings("practice"), session=session)
    client.market_order("USD_JPY", -1000, 158.45, 157.80)
    order = session.calls[0]["json"]["order"]
    assert order["units"] == "-1000"
    assert order["stopLossOnFill"]["price"] == "158.450"
    assert order["takeProfitOnFill"]["price"] == "157.800"
    assert order["timeInForce"] == "FOK"
