from __future__ import annotations

from typing import Any


MODEL = "kfxai-rule-v1"


def _change(candles: list[list[Any]], bars: int) -> float | None:
    if len(candles) <= bars:
        return None
    previous = float(candles[-1 - bars][4])
    latest = float(candles[-1][4])
    return latest / previous - 1.0 if previous else None


def classify_regime(ohlcv_by_instrument: dict[str, list[list[Any]]]) -> dict[str, Any]:
    changes = [
        value for candles in ohlcv_by_instrument.values()
        if (value := _change(candles, 4)) is not None
    ]
    average = sum(changes) / len(changes) if changes else 0.0
    if average > 0.0015:
        regime = "risk_on"
    elif average < -0.0015:
        regime = "risk_off"
    else:
        regime = "neutral"
    return {
        "regime": regime,
        "note": f"4-candle basket return={average * 100:+.3f}%",
        "model": MODEL,
    }


def risk_directive(
    ohlcv_by_instrument: dict[str, list[list[Any]]], history: list[dict[str, Any]],
) -> dict[str, Any]:
    regime = classify_regime(ohlcv_by_instrument)
    directive = "risk_off" if regime["regime"] == "risk_off" else "neutral"
    return {"directive": directive, "note": regime["note"], "model": MODEL}


def review_trade(context: dict[str, Any]) -> dict[str, Any]:
    pnl = float(context.get("pnl_jpy") or 0.0)
    reason = context.get("exit_reason") or "unknown"
    bars = int(context.get("bars_held") or 0)
    if reason == "stop_loss":
        category = "adverse_move"
    elif reason == "max_hold":
        category = "stale_trade"
    elif pnl > 0:
        category = "planned_win"
    else:
        category = "weak_signal"
    return {
        "category": category,
        "verdict": "good" if pnl > 0 else "review",
        "lesson": f"exit={reason}, bars={bars}, pnl_jpy={pnl:+.0f}",
        "model": MODEL,
    }


def propose_hypotheses(dossier: dict[str, Any]) -> list[dict[str, Any]]:
    win_rate = float(dossier.get("win_rate") or 0.0)
    threshold = 0.65 if win_rate < 0.48 else 0.60
    return [{
        "name": "adjust_signal_threshold",
        "parameter": "signal_threshold",
        "value": threshold,
        "rationale": f"observed win_rate={win_rate:.3f}",
    }]

