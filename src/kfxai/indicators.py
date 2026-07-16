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

    # レンジ内位置(直近48本の高値-安値の中でcloseがどこか。0=安値, 1=高値)
    window = view[-48:]
    hi = max(c.high for c in window)
    lo = min(c.low for c in window)
    range_pos = (closes[-1] - lo) / (hi - lo) if hi > lo else 0.5

    # 時間帯(セッション)。FXは東京/ロンドン/NYで挙動が変わる。UTC時刻を巡回エンコード。
    try:
        hour = int(view[-1].time[11:13])
    except (ValueError, IndexError):
        hour = 0
    hour_sin = math.sin(2 * math.pi * hour / 24)
    hour_cos = math.cos(2 * math.pi * hour / 24)

    return {
        "return_1": _return(closes, 1),
        "return_3": _return(closes, 3),
        "return_6": _return(closes, 6),
        "return_12": _return(closes, 12),
        "return_24": _return(closes, 24),   # 6h モメンタム(上位足トレンド代理)
        "return_96": _return(closes, 96),   # 24h モメンタム(上位足トレンド代理)
        "ma_gap": fast / slow - 1.0 if slow else 0.0,
        "volatility": _stdev(returns),
        "range_mean": _mean(ranges),
        "range_pos": range_pos * 2.0 - 1.0,  # -1..+1 に正規化
        "rsi_scaled": (rsi(closes) - 50.0) / 50.0,
        "volume_ratio": volumes[-1] / _mean(volumes[:-1]) - 1.0 if _mean(volumes[:-1]) else 0.0,
        "hour_sin": hour_sin,
        "hour_cos": hour_cos,
    }


