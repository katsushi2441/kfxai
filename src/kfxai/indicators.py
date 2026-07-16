from __future__ import annotations

import math

from .models import Candle


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _return(closes: list[float], bars: int) -> float:
    if len(closes) <= bars or not closes[-1 - bars]:
        return 0.0
    return closes[-1] / closes[-1 - bars] - 1.0


def rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) <= period:
        return 50.0
    changes = [closes[i] - closes[i - 1] for i in range(len(closes) - period, len(closes))]
    gains = _mean([max(change, 0.0) for change in changes])
    losses = _mean([max(-change, 0.0) for change in changes])
    if losses == 0:
        return 100.0 if gains else 50.0
    return 100.0 - 100.0 / (1.0 + gains / losses)


def feature_vector(candles: list[Candle], end: int | None = None) -> dict[str, float]:
    view = candles if end is None else candles[:end]
    closes = [c.close for c in view]
    if len(closes) < 30:
        raise ValueError("at least 30 completed candles are required")
    returns = [closes[i] / closes[i - 1] - 1.0 for i in range(max(1, len(closes) - 20), len(closes))]
    fast = _mean(closes[-5:])
    slow = _mean(closes[-20:])
    ranges = [(c.high - c.low) / c.close for c in view[-14:] if c.close]
    volumes = [float(c.volume) for c in view[-20:]]
    return {
        "return_1": _return(closes, 1),
        "return_3": _return(closes, 3),
        "return_6": _return(closes, 6),
        "return_12": _return(closes, 12),
        "ma_gap": fast / slow - 1.0 if slow else 0.0,
        "volatility": _stdev(returns),
        "range_mean": _mean(ranges),
        "rsi_scaled": (rsi(closes) - 50.0) / 50.0,
        "volume_ratio": volumes[-1] / _mean(volumes[:-1]) - 1.0 if _mean(volumes[:-1]) else 0.0,
    }


