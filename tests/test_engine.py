from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kfxai.config import Settings
from kfxai.database import Database
from kfxai.engine import TradingEngine, market_is_open, price_targets
from kfxai.judgment import RuleBackend
from kfxai.models import Candle, Price


class FakeOanda:
    def candles(self, instrument: str, granularity: str, count: int) -> list[Candle]:
        base = 158.0 if instrument == "USD_JPY" else 171.0
        result = []
        for index in range(count):
            close = base + index * 0.015 + ((index % 5) - 2) * 0.006
            result.append(Candle(
                time=f"2026-07-15T{index // 60:02d}:{index % 60:02d}:00Z",
                open=close - 0.01, high=close + 0.03, low=close - 0.03,
                close=close, volume=100 + index, complete=True,
            ))
        return result

    def prices(self, instruments: tuple[str, ...]) -> dict[str, Price]:
        return {
            instrument: Price(instrument, 161.58, 161.59, "2026-07-16T01:00:00Z")
            for instrument in instruments
        }

    def open_positions(self) -> list[dict[str, object]]:
        return []


def test_weekend_gate() -> None:
    assert not market_is_open(datetime(2026, 7, 18, 10, tzinfo=timezone.utc))
    assert market_is_open(datetime(2026, 7, 16, 10, tzinfo=timezone.utc))


def test_price_targets() -> None:
    price = Price("USD_JPY", 158.10, 158.12, "now")
    stop, take = price_targets(price, "long", 25, 40)
    assert stop == pytest.approx(157.87)
    assert take == pytest.approx(158.52)


def test_full_paper_cycle_persists_result(tmp_path) -> None:
    settings = Settings(
        account_id="test", access_token="test", instruments=("USD_JPY",),
        candle_count=120, signal_threshold=0.51, database_path=tmp_path / "kfxai.sqlite3",
    )
    db = Database(settings.database_path)
    result = TradingEngine(settings, FakeOanda(), db, RuleBackend()).run_cycle()
    assert result["mode"] == "paper"
    assert len(result["actions"]) == 1
    assert db.query("SELECT status FROM cycles") == [{"status": "done"}]
    assert len(db.query("SELECT * FROM decisions")) == 1
