#!/usr/bin/env python3
"""kfxai walk-forward backtest — ライブと同一ゲートで戦略を過去データ検証する。

kfreqaiと同じ規律「取引する前に数字で見る」を kfxai にも入れる土台。
過去だけで学習(walk-forward)→翌バーを予測→engine.pyと同じゲート/決済で約定を
シミュレートし、PnL(JPY)・勝率・取引数・方向的中率を出す。look-ahead防止済み。

使い方:
  set -a; source .env; set +a
  .venv/bin/python scripts/backtest.py --bars 5000 --retrain-every 8

注意: 決済シミュはmid足のhigh/lowを使い、スプレッドはentryにpipコストとして課す。
同一バー内でSLとTPが両方触れた場合はSL優先(保守的)。
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from kfxai.config import load_settings  # noqa: E402
from kfxai.models import Candle  # noqa: E402
from kfxai.oanda import OandaClient  # noqa: E402
from kfxai.predictor import DirectionModel  # noqa: E402
from kfxai.predictor_gbm import GBMDirectionModel  # noqa: E402


def fetch_history(client: OandaClient, instrument: str, granularity: str, total: int) -> list[Candle]:
    """count=5000上限でtoを使い過去方向にページングして total 本集める。"""
    out: list[Candle] = []
    to = None
    while len(out) < total:
        params = {"price": "M", "granularity": granularity, "count": min(5000, total - len(out) + 5)}
        if to:
            params["to"] = to
        data = client._request("GET", f"/v3/instruments/{instrument}/candles", params=params)
        batch = data.get("candles", [])
        if not batch:
            break
        parsed = []
        for item in batch:
            if not item.get("complete", False):
                continue
            m = item["mid"]
            parsed.append(Candle(
                time=item["time"], open=float(m["o"]), high=float(m["h"]),
                low=float(m["l"]), close=float(m["c"]), volume=int(item.get("volume", 0)),
            ))
        if not parsed:
            break
        out = parsed + out
        to = batch[0]["time"]  # 次はこのバッチの最古より前
        if len(batch) < params["count"] - 5:
            break
    # 時系列昇順・重複除去
    seen = set()
    uniq = []
    for c in out:
        if c.time in seen:
            continue
        seen.add(c.time)
        uniq.append(c)
    uniq.sort(key=lambda c: c.time)
    return uniq[-total:]


def simulate_exit(candles: list[Candle], entry_idx: int, side: str, entry: float,
                  pip: float, stop_pips: float, take_pips: float, max_hold: int) -> tuple[float, str, int]:
    """entry_idx で建てて、以降の足で SL/TP/max_hold のどれかで決済。決済価格・理由・保有本数。"""
    if side == "long":
        sl = entry - stop_pips * pip
        tp = entry + take_pips * pip
    else:
        sl = entry + stop_pips * pip
        tp = entry - take_pips * pip
    for held in range(1, max_hold + 1):
        j = entry_idx + held
        if j >= len(candles):
            return candles[-1].close, "eod", held
        c = candles[j]
        if side == "long":
            if c.low <= sl:
                return sl, "stop_loss", held
            if c.high >= tp:
                return tp, "take_profit", held
        else:
            if c.high >= sl:
                return sl, "stop_loss", held
            if c.low <= tp:
                return tp, "take_profit", held
    return candles[entry_idx + max_hold].close, "max_hold", max_hold


def pnl_jpy(instrument: str, side: str, units: int, open_p: float, close_p: float, usdjpy: float) -> float:
    direction = 1.0 if side == "long" else -1.0
    quote_pnl = (close_p - open_p) * units * direction
    if instrument.endswith("_JPY"):
        return quote_pnl
    quote = instrument.split("_", 1)[1]
    if quote == "USD":
        return quote_pnl * usdjpy
    return quote_pnl


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=5000, help="1銘柄あたり取得M15本数(5000≈52日)")
    ap.add_argument("--train-window", type=int, default=240, help="学習窓(ライブのcandle_count=240に一致)")
    ap.add_argument("--retrain-every", type=int, default=8, help="N本ごとに再学習(1=毎バー・最も忠実だが遅い)")
    ap.add_argument("--model", choices=["logistic", "lgbm"], default="logistic")
    args = ap.parse_args()
    ModelClass = GBMDirectionModel if args.model == "lgbm" else DirectionModel

    s = load_settings()
    if not s.account_id or not s.access_token:
        print("OANDA creds not set (.env)")
        return 1
    client = OandaClient(s)

    thr = s.signal_threshold
    pip_cost = min(1.0, s.max_spread_pips)  # entryに課すスプレッドコスト(pip)。保守的に1pip上限
    tw = args.train_window

    print(f"config: instruments={list(s.instruments)} bars={args.bars} tw={tw} "
          f"thr={thr} SL={s.stop_loss_pips} TP={s.take_profit_pips} maxhold={s.max_hold_candles} "
          f"retrain_every={args.retrain_every} spread_cost={pip_cost}pip")

    # USD_JPY履歴を時刻→closeで引けるように(EUR_USD換算用)
    hist: dict[str, list[Candle]] = {}
    for inst in s.instruments:
        hist[inst] = fetch_history(client, inst, s.granularity, args.bars)
        print(f"  fetched {inst}: {len(hist[inst])} candles "
              f"[{hist[inst][0].time[:10]}..{hist[inst][-1].time[:10]}]")
    usdjpy_map = {c.time: c.close for c in hist.get("USD_JPY", [])}

    def usdjpy_at(t: str) -> float:
        return usdjpy_map.get(t, 150.0)

    results = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0, "correct_dir": 0, "tp": 0, "sl": 0})
    pip_of = {inst: (0.01 if inst.endswith("_JPY") else 0.0001) for inst in s.instruments}

    for inst in s.instruments:
        candles = hist[inst]
        pip = pip_of[inst]
        model = ModelClass()
        last_fit = -10**9
        i = tw
        while i < len(candles) - 1:
            if i - last_fit >= args.retrain_every:
                model.fit(
                    candles[i - tw:i], epochs=160,
                    pip_size=pip, stop_pips=s.stop_loss_pips,
                    take_pips=s.take_profit_pips, max_hold=s.max_hold_candles,
                )
                last_fit = i
            p_up, _ = model.predict(candles[:i + 1])
            side = None
            if p_up >= thr:
                side = "long"
            elif p_up <= 1.0 - thr:
                side = "short"
            if side is None:
                i += 1
                continue
            # entry: 現バーclose + スプレッドコスト(不利方向)
            base = candles[i].close
            entry = base + pip_cost * pip if side == "long" else base - pip_cost * pip
            close_p, reason, held = simulate_exit(
                candles, i, side, entry, pip, s.stop_loss_pips, s.take_profit_pips, s.max_hold_candles)
            gain = pnl_jpy(inst, side, s.base_units, entry, close_p, usdjpy_at(candles[i].time))
            r = results[inst]
            r["trades"] += 1
            r["pnl"] += gain
            if gain > 0:
                r["wins"] += 1
            if reason == "take_profit":
                r["tp"] += 1
            elif reason == "stop_loss":
                r["sl"] += 1
            # 方向的中: 次バーの実際の向きと一致したか
            actual_up = candles[i + 1].close > candles[i].close
            if (side == "long") == actual_up:
                r["correct_dir"] += 1
            # 保有中は次エントリーを詰めない(1銘柄1ポジ相当): 決済後の次バーへ
            i += held + 1

    # 集計
    print("\n=== per-instrument ===")
    tot = {"trades": 0, "wins": 0, "pnl": 0.0, "correct_dir": 0, "tp": 0, "sl": 0}
    for inst in s.instruments:
        r = results[inst]
        for k in tot:
            tot[k] += r[k]
        wr = 100 * r["wins"] / r["trades"] if r["trades"] else 0
        da = 100 * r["correct_dir"] / r["trades"] if r["trades"] else 0
        print(f"  {inst:8s} trades={r['trades']:4d} win%={wr:5.1f} dir%={da:5.1f} "
              f"TP={r['tp']:3d} SL={r['sl']:3d} pnl_jpy={r['pnl']:+10.0f}")

    wr = 100 * tot["wins"] / tot["trades"] if tot["trades"] else 0
    da = 100 * tot["correct_dir"] / tot["trades"] if tot["trades"] else 0
    be = 100 * s.stop_loss_pips / (s.stop_loss_pips + s.take_profit_pips)  # スプレッド無視の損益分岐勝率
    print("\n=== TOTAL ===")
    print(f"  trades={tot['trades']} win%={wr:.1f} dir_acc%={da:.1f} "
          f"TP={tot['tp']} SL={tot['sl']} pnl_jpy={tot['pnl']:+.0f}")
    print(f"  breakeven win% (SL/(SL+TP), スプレッド無視)= {be:.1f}  → win%がこれを上回れば優位")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
