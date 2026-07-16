#!/usr/bin/env python3
"""セッションブレイクアウトのwalk-forward検証(ルールベース・MLなし)。

東京レンジ(00:00-07:00 UTC)を、ロンドン時間(07:00-12:00 UTC)にブレイクした方向へ
順張り。SL=レンジ反対側(上限cap)、TP=レンジ幅×倍率、21:00 UTCで強制手仕舞い。
1銘柄1日1取引。スプレッドはentryに1pip課す。

使い方:
  set -a; source .env; set +a
  .venv/bin/python scripts/backtest_session.py --bars 20000
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from kfxai.config import load_settings  # noqa: E402
from kfxai.oanda import OandaClient  # noqa: E402

# fetch_history / pnl_jpy は方向モデル版バックテストと共通
sys.path.insert(0, os.path.dirname(__file__))
from backtest import fetch_history, pnl_jpy  # noqa: E402


def hour_of(t: str) -> int:
    return int(t[11:13])


def day_of(t: str) -> str:
    return t[:10]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=20000, help="M15本数(20000≈208日)")
    ap.add_argument("--range-start", type=int, default=0, help="レンジ開始 UTC時")
    ap.add_argument("--range-end", type=int, default=7, help="レンジ終了 UTC時(この時刻の手前まで)")
    ap.add_argument("--entry-until", type=int, default=12, help="この UTC時までにブレイクしなければ見送り")
    ap.add_argument("--close-hour", type=int, default=21, help="強制手仕舞い UTC時")
    ap.add_argument("--buffer-pips", type=float, default=2.0, help="ブレイク確認バッファ")
    ap.add_argument("--tp-mult", type=float, default=1.0, help="TP=レンジ幅×この倍率")
    ap.add_argument("--max-sl-pips", type=float, default=40.0, help="SL上限(レンジが広すぎる日は見送り)")
    ap.add_argument("--min-range-pips", type=float, default=10.0, help="レンジ幅下限(狭すぎはノイズ)")
    args = ap.parse_args()

    s = load_settings()
    client = OandaClient(s)
    spread_cost = 1.0  # pips

    print(f"config: instruments={list(s.instruments)} bars={args.bars} "
          f"range={args.range_start}-{args.range_end}UTC entry<={args.entry_until}UTC "
          f"close={args.close_hour}UTC buf={args.buffer_pips}p tp={args.tp_mult}xR "
          f"maxSL={args.max_sl_pips}p minR={args.min_range_pips}p units={s.base_units}")

    hist = {}
    for inst in s.instruments:
        hist[inst] = fetch_history(client, inst, "M15", args.bars)
        print(f"  fetched {inst}: {len(hist[inst])} candles "
              f"[{hist[inst][0].time[:10]}..{hist[inst][-1].time[:10]}]")
    usdjpy_map = {c.time: c.close for c in hist.get("USD_JPY", [])}

    def usdjpy_at(t: str) -> float:
        return usdjpy_map.get(t, 150.0)

    results = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0, "tp": 0, "sl": 0, "to": 0, "days_skipped": 0})

    for inst in s.instruments:
        candles = hist[inst]
        pip = 0.01 if inst.endswith("_JPY") else 0.0001
        r = results[inst]
        # 日ごとにグループ化
        days: dict[str, list] = defaultdict(list)
        for idx, c in enumerate(candles):
            days[day_of(c.time)].append((idx, c))
        for day in sorted(days):
            rows = days[day]
            rng = [c for _, c in rows if args.range_start <= hour_of(c.time) < args.range_end]
            if len(rng) < 20:  # レンジ時間帯のデータが欠けている日(週末跨ぎ等)
                continue
            hi = max(c.high for c in rng)
            lo = min(c.low for c in rng)
            range_pips = (hi - lo) / pip
            if range_pips < args.min_range_pips or range_pips > args.max_sl_pips:
                r["days_skipped"] += 1
                continue
            buf = args.buffer_pips * pip
            # ロンドン時間にブレイク探索
            trade = None
            for idx, c in rows:
                h = hour_of(c.time)
                if h < args.range_end or h >= args.entry_until:
                    continue
                if c.close > hi + buf:
                    trade = ("long", idx, c.close + spread_cost * pip)
                    break
                if c.close < lo - buf:
                    trade = ("short", idx, c.close - spread_cost * pip)
                    break
            if trade is None:
                continue
            side, eidx, entry = trade
            rheight = hi - lo
            if side == "long":
                sl_p, tp_p = lo, entry + rheight * args.tp_mult
            else:
                sl_p, tp_p = hi, entry - rheight * args.tp_mult
            # 決済シミュ(同一バーはSL優先=保守的)
            close_p, reason = None, None
            for j in range(eidx + 1, len(candles)):
                c = candles[j]
                if day_of(c.time) != day or hour_of(c.time) >= args.close_hour:
                    close_p, reason = candles[j - 1].close, "timeout"
                    break
                if side == "long":
                    if c.low <= sl_p:
                        close_p, reason = sl_p, "stop_loss"
                        break
                    if c.high >= tp_p:
                        close_p, reason = tp_p, "take_profit"
                        break
                else:
                    if c.high >= sl_p:
                        close_p, reason = sl_p, "stop_loss"
                        break
                    if c.low <= tp_p:
                        close_p, reason = tp_p, "take_profit"
                        break
            if close_p is None:
                close_p, reason = candles[-1].close, "eod"
            gain = pnl_jpy(inst, side, s.base_units, entry, close_p, usdjpy_at(candles[eidx].time))
            r["trades"] += 1
            r["pnl"] += gain
            if gain > 0:
                r["wins"] += 1
            if reason == "take_profit":
                r["tp"] += 1
            elif reason == "stop_loss":
                r["sl"] += 1
            else:
                r["to"] += 1

    print("\n=== per-instrument ===")
    tot = {"trades": 0, "wins": 0, "pnl": 0.0, "tp": 0, "sl": 0, "to": 0}
    for inst in s.instruments:
        r = results[inst]
        for k in tot:
            tot[k] += r[k]
        wr = 100 * r["wins"] / r["trades"] if r["trades"] else 0
        print(f"  {inst:8s} trades={r['trades']:4d} win%={wr:5.1f} "
              f"TP={r['tp']:3d} SL={r['sl']:3d} TO={r['to']:3d} pnl_jpy={r['pnl']:+10.0f}")
    wr = 100 * tot["wins"] / tot["trades"] if tot["trades"] else 0
    print("\n=== TOTAL ===")
    print(f"  trades={tot['trades']} win%={wr:.1f} TP={tot['tp']} SL={tot['sl']} "
          f"TO={tot['to']} pnl_jpy={tot['pnl']:+.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
