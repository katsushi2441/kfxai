from __future__ import annotations

from typing import Any

import requests

from .config import Settings
from .models import Candle, Price


class OandaError(RuntimeError):
    pass


class OandaClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self.settings = settings
        self.session = session or requests.Session()

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.access_token}",
            "Content-Type": "application/json",
            "Accept-Datetime-Format": "RFC3339",
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        self.settings.validate(require_credentials=True)
        response = self.session.request(
            method,
            self.settings.api_base_url + path,
            headers=self.headers,
            timeout=kwargs.pop("timeout", 30),
            **kwargs,
        )
        if not response.ok:
            request_id = response.headers.get("RequestID", "")
            try:
                detail = response.json()
            except ValueError:
                detail = response.text[:500]
            raise OandaError(f"OANDA {response.status_code} request_id={request_id}: {detail}")
        return response.json()

    def candles(self, instrument: str, granularity: str = "M15", count: int = 240) -> list[Candle]:
        data = self._request(
            "GET",
            f"/v3/instruments/{instrument}/candles",
            params={"price": "M", "granularity": granularity, "count": count},
        )
        candles: list[Candle] = []
        for item in data.get("candles", []):
            mid = item.get("mid") or {}
            if not mid:
                continue
            candles.append(
                Candle(
                    time=item["time"],
                    open=float(mid["o"]),
                    high=float(mid["h"]),
                    low=float(mid["l"]),
                    close=float(mid["c"]),
                    volume=int(item.get("volume", 0)),
                    complete=bool(item.get("complete", False)),
                )
            )
        return candles

    def prices(self, instruments: tuple[str, ...] | list[str]) -> dict[str, Price]:
        data = self._request(
            "GET",
            f"/v3/accounts/{self.settings.account_id}/pricing",
            params={"instruments": ",".join(instruments), "includeUnitsAvailable": "false"},
        )
        result: dict[str, Price] = {}
        for item in data.get("prices", []):
            bids = item.get("bids") or []
            asks = item.get("asks") or []
            if not bids or not asks:
                continue
            price = Price(
                instrument=item["instrument"],
                bid=float(bids[0]["price"]),
                ask=float(asks[0]["price"]),
                time=item["time"],
                status=item.get("status", "tradeable"),
            )
            result[price.instrument] = price
        return result

    def account_summary(self) -> dict[str, Any]:
        return self._request("GET", f"/v3/accounts/{self.settings.account_id}/summary").get(
            "account", {}
        )

    def open_positions(self) -> list[dict[str, Any]]:
        data = self._request("GET", f"/v3/accounts/{self.settings.account_id}/openPositions")
        return list(data.get("positions") or [])

    def market_order(
        self,
        instrument: str,
        units: int,
        stop_loss_price: float,
        take_profit_price: float,
    ) -> dict[str, Any]:
        if self.settings.trading_mode not in {"practice", "live"}:
            raise OandaError("broker orders are disabled in paper mode")
        precision = 3 if instrument.endswith("_JPY") else 5
        order = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(units),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
                "stopLossOnFill": {"price": f"{stop_loss_price:.{precision}f}", "timeInForce": "GTC"},
                "takeProfitOnFill": {
                    "price": f"{take_profit_price:.{precision}f}",
                    "timeInForce": "GTC",
                },
            }
        }
        return self._request(
            "POST", f"/v3/accounts/{self.settings.account_id}/orders", json=order
        )

    def health(self) -> dict[str, Any]:
        summary = self.account_summary()
        return {
            "ok": True,
            "environment": self.settings.oanda_environment,
            "account_currency": summary.get("currency"),
            "nav": summary.get("NAV"),
            "open_trade_count": summary.get("openTradeCount"),
        }
