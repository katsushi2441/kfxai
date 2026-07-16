from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Candle:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    complete: bool = True

    def as_ohlcv(self) -> list[Any]:
        return [self.time, self.open, self.high, self.low, self.close, self.volume]


@dataclass(frozen=True)
class Price:
    instrument: str
    bid: float
    ask: float
    time: str
    status: str = "tradeable"

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def pip_size(self) -> float:
        return 0.01 if self.instrument.endswith("_JPY") else 0.0001

    @property
    def spread_pips(self) -> float:
        return (self.ask - self.bid) / self.pip_size


@dataclass(frozen=True)
class Signal:
    instrument: str
    action: str
    probability_up: float
    confidence: float
    regime: str
    directive: str
    reason: str
    model: str
    features: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

