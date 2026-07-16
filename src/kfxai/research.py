from __future__ import annotations

import json
from typing import Any

from .config import load_settings
from .database import Database
from .judgment import build_backend
from .models import utc_now_iso
from .predictor import DirectionModel


def dossier(db: Database) -> dict[str, Any]:
    trades = db.query("SELECT * FROM paper_trades WHERE status='closed' ORDER BY id")
    wins = [trade for trade in trades if float(trade.get("pnl_jpy") or 0) > 0]
    return {
        "closed_trades": len(trades),
        "wins": len(wins),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "pnl_jpy": sum(float(trade.get("pnl_jpy") or 0) for trade in trades),
        "lessons": db.query("SELECT category,verdict,lesson FROM journal ORDER BY id DESC LIMIT 50"),
    }


def evaluate_threshold(db: Database, instruments: tuple[str, ...], granularity: str, threshold: float) -> dict[str, Any]:
    correct = 0
    signals = 0
    for instrument in instruments:
        candles = db.load_candles(instrument, granularity, 2000)
        if len(candles) < 100:
            continue
        for end in range(80, len(candles) - 1, 4):
            model = DirectionModel()
            model.fit(candles[:end], epochs=80)
            probability, _ = model.predict(candles[:end])
            predicted = 1 if probability >= threshold else -1 if probability <= 1 - threshold else 0
            if not predicted:
                continue
            actual = 1 if candles[end].close > candles[end - 1].close else -1
            correct += int(predicted == actual)
            signals += 1
    return {
        "threshold": threshold,
        "signals": signals,
        "correct": correct,
        "accuracy": correct / signals if signals else 0.0,
    }


def run_research() -> dict[str, Any]:
    settings = load_settings()
    db = Database(settings.database_path)
    evidence = dossier(db)
    hypotheses = build_backend(settings).propose_hypotheses(evidence)
    baseline = evaluate_threshold(db, settings.instruments, settings.granularity, settings.signal_threshold)
    results = []
    for hypothesis in hypotheses[:3]:
        if hypothesis.get("parameter") != "signal_threshold":
            continue
        value = float(hypothesis.get("value"))
        if not 0.55 <= value <= 0.75:
            continue
        measured = evaluate_threshold(db, settings.instruments, settings.granularity, value)
        verdict = "candidate" if measured["signals"] >= 20 and measured["accuracy"] > baseline["accuracy"] else "rejected"
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO research(created_at,hypothesis_json,baseline_json,result_json,verdict) "
                "VALUES(?,?,?,?,?)",
                (
                    utc_now_iso(), json.dumps(hypothesis, ensure_ascii=False),
                    json.dumps(baseline), json.dumps(measured), verdict,
                ),
            )
        results.append({"hypothesis": hypothesis, "measurement": measured, "verdict": verdict})
    return {"dossier": evidence, "baseline": baseline, "results": results}


def main() -> int:
    print(json.dumps(run_research(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

