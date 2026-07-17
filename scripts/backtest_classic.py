#!/usr/bin/env python3
"""古典FX戦略の一括バックテスト(2026-07-17)。

je-suis-tm/quant-trading(Apache-2.0)等で整理されている古典戦略群を
kfxaiの検証手法(M15・OANDA実データ・スプレッド課金・walk-forwardなし=
ルールベースなのでリーク無し)に載せ、複数ペアで一括選別する。

目的: 「1戦略ずつpaperで数ヶ月」の直列検証をやめ、バックテストで
大量並列に落第させ、生き残りだけをアリーナ(paper)の最終関門に送る。

- 判断は バーiの終値 で行い、約定は バーi+1の始値。
- スプレッドはエントリー時にペア別の保守的pipsを課す。
- SLとTPが同一バー内で両方かかる場合はSL優先(保守的)。
- 非JPYペアはUSDクォートのみ(pnl_jpyがUSD→JPY換算対応のため)。
- ライブアリーナ稼働中のdonchian/rsi_meanrev/ma_crossと同パラメータの
  ポートも含む(現行アリーナ戦略の初の歴史的裏付けを取る)。

使い方:
  set -a; source .env; set +a
  .venv/bin/python scripts/backtest_classic.py --bars 40000
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from kfxai.config import load_settings  # noqa: E402
from kfxai.oanda import OandaClient  # noqa: E402
from backtest import fetch_history, pnl_jpy  # noqa: E402

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "backtest_cache")

INSTRUMENTS = ["USD_JPY", "EUR_JPY", "GBP_JPY", "EUR_USD", "GBP_USD", "AUD_USD"]
SPREAD_PIPS = {"USD_JPY": 1.0, "EUR_JPY": 1.4, "GBP_JPY": 1.8,
               "EUR_USD": 0.9, "GBP_USD": 1.2, "AUD_USD": 1.1}
UNITS = 1000


# ---------- data ----------

def load_candles(client, inst, bars):
    path = os.path.join(CACHE_DIR, "%s_M15_%d.json" % (inst, bars))
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    candles = fetch_history(client, inst, "M15", bars)
    data = [[c.time, c.open, c.high, c.low, c.close] for c in candles]
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)
    return data


# ---------- indicators (numpy, 事前計算) ----------

def ema(x, n):
    a = 2.0 / (n + 1)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def sma(x, n):
    out = np.full(len(x), np.nan)
    c = np.cumsum(np.insert(x, 0, 0.0))
    out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def rsi(close, n):
    d = np.diff(close, prepend=close[0])
    up = np.where(d > 0, d, 0.0)
    dn = np.where(d < 0, -d, 0.0)
    ru = ema(up, 2 * n - 1)
    rd = ema(dn, 2 * n - 1)
    rs = np.divide(ru, rd, out=np.full_like(ru, np.inf), where=rd != 0)
    return 100 - 100 / (1 + rs)


def atr(high, low, close, n):
    pc = np.roll(close, 1)
    pc[0] = close[0]
    tr = np.maximum(high - low, np.maximum(abs(high - pc), abs(low - pc)))
    return ema(tr, 2 * n - 1)


def rolling_max(x, n):
    out = np.full(len(x), np.nan)
    for i in range(n - 1, len(x)):
        out[i] = x[i - n + 1:i + 1].max()
    return out


def rolling_min(x, n):
    out = np.full(len(x), np.nan)
    for i in range(n - 1, len(x)):
        out[i] = x[i - n + 1:i + 1].min()
    return out


def psar(high, low, af_step=0.02, af_max=0.2):
    n = len(high)
    sar = np.zeros(n)
    trend = np.ones(n, dtype=int)  # 1=up -1=down
    sar[0] = low[0]
    ep = high[0]
    af = af_step
    for i in range(1, n):
        sar[i] = sar[i - 1] + af * (ep - sar[i - 1])
        if trend[i - 1] == 1:
            if low[i] < sar[i]:
                trend[i] = -1
                sar[i] = ep
                ep = low[i]
                af = af_step
            else:
                trend[i] = 1
                if high[i] > ep:
                    ep = high[i]
                    af = min(af + af_step, af_max)
        else:
            if high[i] > sar[i]:
                trend[i] = 1
                sar[i] = ep
                ep = high[i]
                af = af_step
            else:
                trend[i] = -1
                if low[i] < ep:
                    ep = low[i]
                    af = min(af + af_step, af_max)
    return trend


# ---------- strategy definitions ----------
# 各戦略: prepare(arrs)->state, signal(state,i)->None or dict(
#   side, sl(価格), tp(価格 or None), max_hold(バー数 or None), eod(bool))

class Strat:
    name = "base"

    def prepare(self, o, h, l, c, times):
        raise NotImplementedError

    def signal(self, st, i):
        raise NotImplementedError


class DualThrust(Strat):
    """Dual Thrust(Michael Chalek): 前日レンジ×K のブレイクで順張り、当日中に手仕舞い。"""
    name = "dual_thrust"
    K = 0.5

    def prepare(self, o, h, l, c, times):
        day = np.array([t[:10] for t in times])
        hour = np.array([int(t[11:13]) for t in times])
        # 日別 HH/LL/HC/LC
        stats = {}
        for d in np.unique(day):
            m = day == d
            stats[d] = (h[m].max(), l[m].min(), c[m][-1], c[m][0])
        days_sorted = sorted(stats)
        prev = {}
        for i in range(1, len(days_sorted)):
            hh, ll, cc, _ = stats[days_sorted[i - 1]]
            rng = max(hh - min(cc, ll), max(cc, hh) - ll)
            prev[days_sorted[i]] = rng
        day_open = {}
        for d in np.unique(day):
            m = np.where(day == d)[0]
            day_open[d] = o[m[0]]
        return {"day": day, "hour": hour, "prev_range": prev, "day_open": day_open,
                "o": o, "c": c}

    def signal(self, st, i):
        d = st["day"][i]
        rng = st["prev_range"].get(d)
        if rng is None or st["hour"][i] >= 20:
            return None
        up = st["day_open"][d] + self.K * rng
        dn = st["day_open"][d] - self.K * rng
        c = st["c"][i]
        if c > up:
            return {"side": "long", "sl": dn, "tp": None, "max_hold": None, "eod": True}
        if c < dn:
            return {"side": "short", "sl": up, "tp": None, "max_hold": None, "eod": True}
        return None


class BollingerMR(Strat):
    """ボリンジャー(80,2σ)逆張り: バンド外→内側へ戻ったら逆張り、ミッドで利確。"""
    name = "bollinger_mr"

    def prepare(self, o, h, l, c, times):
        mid = sma(c, 80)
        sd = np.full(len(c), np.nan)
        for i in range(79, len(c)):
            sd[i] = c[i - 79:i + 1].std()
        return {"c": c, "mid": mid, "up": mid + 2 * sd, "dn": mid - 2 * sd,
                "atr": atr(h, l, c, 56)}

    def signal(self, st, i):
        if i < 81 or np.isnan(st["mid"][i]):
            return None
        c0, c1 = st["c"][i - 1], st["c"][i]
        if c0 < st["dn"][i - 1] and c1 > st["dn"][i]:
            return {"side": "long", "sl": c1 - 1.5 * st["atr"][i],
                    "tp": st["mid"][i], "max_hold": 64, "eod": False}
        if c0 > st["up"][i - 1] and c1 < st["up"][i]:
            return {"side": "short", "sl": c1 + 1.5 * st["atr"][i],
                    "tp": st["mid"][i], "max_hold": 64, "eod": False}
        return None


class SarTrend(Strat):
    """Parabolic SARフリップ順張り。逆フリップかSLで手仕舞い。"""
    name = "sar_trend"

    def prepare(self, o, h, l, c, times):
        return {"trend": psar(h, l), "c": c, "atr": atr(h, l, c, 56)}

    def signal(self, st, i):
        if i < 100:
            return None
        t0, t1 = st["trend"][i - 1], st["trend"][i]
        if t0 == -1 and t1 == 1:
            return {"side": "long", "sl": st["c"][i] - 2 * st["atr"][i],
                    "tp": None, "max_hold": 192, "eod": False, "flip_exit": True}
        if t0 == 1 and t1 == -1:
            return {"side": "short", "sl": st["c"][i] + 2 * st["atr"][i],
                    "tp": None, "max_hold": 192, "eod": False, "flip_exit": True}
        return None

    def flip(self, st, i, side):
        return (side == "long" and st["trend"][i] == -1) or \
               (side == "short" and st["trend"][i] == 1)


class MacdCross(Strat):
    """MACD(48,104,36 ≒ H1の12,26,9)シグナルクロス+ゼロラインフィルタ。"""
    name = "macd_cross"

    def prepare(self, o, h, l, c, times):
        macd = ema(c, 48) - ema(c, 104)
        sig = ema(macd, 36)
        return {"macd": macd, "sig": sig, "c": c, "atr": atr(h, l, c, 56)}

    def signal(self, st, i):
        if i < 150:
            return None
        m0, m1 = st["macd"][i - 1], st["macd"][i]
        s0, s1 = st["sig"][i - 1], st["sig"][i]
        if m0 <= s0 and m1 > s1 and m1 < 0:
            return {"side": "long", "sl": st["c"][i] - 2 * st["atr"][i],
                    "tp": st["c"][i] + 3 * st["atr"][i], "max_hold": 192, "eod": False}
        if m0 >= s0 and m1 < s1 and m1 > 0:
            return {"side": "short", "sl": st["c"][i] + 2 * st["atr"][i],
                    "tp": st["c"][i] - 3 * st["atr"][i], "max_hold": 192, "eod": False}
        return None


class Rsi2MR(Strat):
    """Connors RSI(2)型: 長期トレンド方向の押し目だけ逆張り。RSI(8バー=2h)。"""
    name = "rsi2_mr"

    def prepare(self, o, h, l, c, times):
        return {"rsi": rsi(c, 8), "sma": sma(c, 800), "c": c,
                "atr": atr(h, l, c, 56)}

    def signal(self, st, i):
        if i < 810 or np.isnan(st["sma"][i]):
            return None
        c = st["c"][i]
        if st["rsi"][i] < 10 and c > st["sma"][i]:
            return {"side": "long", "sl": c - 2 * st["atr"][i],
                    "tp": None, "max_hold": 32, "eod": False, "rsi_exit": 50}
        if st["rsi"][i] > 90 and c < st["sma"][i]:
            return {"side": "short", "sl": c + 2 * st["atr"][i],
                    "tp": None, "max_hold": 32, "eod": False, "rsi_exit": 50}
        return None

    def rsi_exit_hit(self, st, i, side):
        return (side == "long" and st["rsi"][i] > 50) or \
               (side == "short" and st["rsi"][i] < 50)


class Turtle(Strat):
    """タートル型: 320本(≈3.3日)高値/安値ブレイクで順張り、80本逆チャネルで手仕舞い。"""
    name = "turtle_320_80"

    def prepare(self, o, h, l, c, times):
        return {"hi320": rolling_max(h, 320), "lo320": rolling_min(l, 320),
                "hi80": rolling_max(h, 80), "lo80": rolling_min(l, 80),
                "c": c, "atr": atr(h, l, c, 56)}

    def signal(self, st, i):
        if i < 330:
            return None
        c = st["c"][i]
        if c > st["hi320"][i - 1]:
            return {"side": "long", "sl": c - 2 * st["atr"][i],
                    "tp": None, "max_hold": 480, "eod": False, "chan_exit": True}
        if c < st["lo320"][i - 1]:
            return {"side": "short", "sl": c + 2 * st["atr"][i],
                    "tp": None, "max_hold": 480, "eod": False, "chan_exit": True}
        return None

    def chan_exit_hit(self, st, i, side):
        c = st["c"][i]
        return (side == "long" and c < st["lo80"][i - 1]) or \
               (side == "short" and c > st["hi80"][i - 1])


class KeltnerBreak(Strat):
    """ケルトナー(EMA80±2×ATR80)ブレイク順張り、ミッド割れで手仕舞い。"""
    name = "keltner_break"

    def prepare(self, o, h, l, c, times):
        mid = ema(c, 80)
        a = atr(h, l, c, 80)
        return {"mid": mid, "up": mid + 2 * a, "dn": mid - 2 * a, "c": c,
                "atr": atr(h, l, c, 56)}

    def signal(self, st, i):
        if i < 100:
            return None
        c0, c1 = st["c"][i - 1], st["c"][i]
        if c0 <= st["up"][i - 1] and c1 > st["up"][i]:
            return {"side": "long", "sl": st["mid"][i], "tp": None,
                    "max_hold": 192, "eod": False, "mid_exit": True}
        if c0 >= st["dn"][i - 1] and c1 < st["dn"][i]:
            return {"side": "short", "sl": st["mid"][i], "tp": None,
                    "max_hold": 192, "eod": False, "mid_exit": True}
        return None

    def mid_exit_hit(self, st, i, side):
        c = st["c"][i]
        return (side == "long" and c < st["mid"][i]) or \
               (side == "short" and c > st["mid"][i])


class HeikinTrend(Strat):
    """平均足3本連続同色で順張り、色反転で手仕舞い。"""
    name = "heikin_trend"

    def prepare(self, o, h, l, c, times):
        ha_c = (o + h + l + c) / 4
        ha_o = np.empty_like(ha_c)
        ha_o[0] = o[0]
        for i in range(1, len(o)):
            ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2
        color = np.where(ha_c > ha_o, 1, -1)
        return {"color": color, "c": c, "atr": atr(h, l, c, 56)}

    def signal(self, st, i):
        if i < 100:
            return None
        col = st["color"]
        if col[i] == 1 and col[i - 1] == 1 and col[i - 2] == 1 and col[i - 3] != 1:
            return {"side": "long", "sl": st["c"][i] - 2 * st["atr"][i],
                    "tp": None, "max_hold": 192, "eod": False, "color_exit": True}
        if col[i] == -1 and col[i - 1] == -1 and col[i - 2] == -1 and col[i - 3] != -1:
            return {"side": "short", "sl": st["c"][i] + 2 * st["atr"][i],
                    "tp": None, "max_hold": 192, "eod": False, "color_exit": True}
        return None

    def color_exit_hit(self, st, i, side):
        return (side == "long" and st["color"][i] == -1) or \
               (side == "short" and st["color"][i] == 1)


class AoZero(Strat):
    """Awesome Oscillator(20,136 ≒ H1の5,34)ゼロクロス順張り。"""
    name = "ao_zero"

    def prepare(self, o, h, l, c, times):
        med = (h + l) / 2
        ao = sma(med, 20) - sma(med, 136)
        return {"ao": ao, "c": c, "atr": atr(h, l, c, 56)}

    def signal(self, st, i):
        if i < 150 or np.isnan(st["ao"][i - 1]):
            return None
        a0, a1 = st["ao"][i - 1], st["ao"][i]
        if a0 <= 0 < a1:
            return {"side": "long", "sl": st["c"][i] - 2 * st["atr"][i],
                    "tp": st["c"][i] + 3 * st["atr"][i], "max_hold": 192, "eod": False}
        if a0 >= 0 > a1:
            return {"side": "short", "sl": st["c"][i] + 2 * st["atr"][i],
                    "tp": st["c"][i] - 3 * st["atr"][i], "max_hold": 192, "eod": False}
        return None


# ---- ライブアリーナ稼働中戦略の同パラメータ・ポート(歴史的裏付け用) ----

class LiveDonchian(Strat):
    """live donchian: 96本チャネルブレイク、SL2×ATR、TP3×ATR、24h。"""
    name = "live_donchian"

    def prepare(self, o, h, l, c, times):
        return {"hi": rolling_max(h, 96), "lo": rolling_min(l, 96), "c": c,
                "atr": atr(h, l, c, 56)}

    def signal(self, st, i):
        if i < 100:
            return None
        c = st["c"][i]
        if c > st["hi"][i - 1]:
            return {"side": "long", "sl": c - 2 * st["atr"][i],
                    "tp": c + 3 * st["atr"][i], "max_hold": 96, "eod": False}
        if c < st["lo"][i - 1]:
            return {"side": "short", "sl": c + 2 * st["atr"][i],
                    "tp": c - 3 * st["atr"][i], "max_hold": 96, "eod": False}
        return None


class LiveRsiMR(Strat):
    """live rsi_meanrev: RSI(14)28/72逆張り、SL20p/TP25p、8h。"""
    name = "live_rsi_meanrev"

    def prepare(self, o, h, l, c, times):
        return {"rsi": rsi(c, 14), "c": c}

    def signal(self, st, i):
        if i < 30:
            return None
        c = st["c"][i]
        pip = 0.01 if c > 10 else 0.0001
        if st["rsi"][i] < 28:
            return {"side": "long", "sl": c - 20 * pip, "tp": c + 25 * pip,
                    "max_hold": 32, "eod": False}
        if st["rsi"][i] > 72:
            return {"side": "short", "sl": c + 20 * pip, "tp": c - 25 * pip,
                    "max_hold": 32, "eod": False}
        return None


class LiveMaCross(Strat):
    """live ma_cross: EMA20/80クロス、SL25p/TP40p、24h。"""
    name = "live_ma_cross"

    def prepare(self, o, h, l, c, times):
        return {"f": ema(c, 20), "s": ema(c, 80), "c": c}

    def signal(self, st, i):
        if i < 100:
            return None
        f0, f1 = st["f"][i - 1], st["f"][i]
        s0, s1 = st["s"][i - 1], st["s"][i]
        c = st["c"][i]
        pip = 0.01 if c > 10 else 0.0001
        if f0 <= s0 and f1 > s1:
            return {"side": "long", "sl": c - 25 * pip, "tp": c + 40 * pip,
                    "max_hold": 96, "eod": False}
        if f0 >= s0 and f1 < s1:
            return {"side": "short", "sl": c + 25 * pip, "tp": c - 40 * pip,
                    "max_hold": 96, "eod": False}
        return None


STRATEGIES = [DualThrust(), BollingerMR(), SarTrend(), MacdCross(), Rsi2MR(),
              Turtle(), KeltnerBreak(), HeikinTrend(), AoZero(),
              LiveDonchian(), LiveRsiMR(), LiveMaCross()]


# ---------- simulation ----------

def simulate(strat, inst, data, usdjpy_map):
    times = [r[0] for r in data]
    o = np.array([r[1] for r in data])
    h = np.array([r[2] for r in data])
    l = np.array([r[3] for r in data])
    c = np.array([r[4] for r in data])
    st = strat.prepare(o, h, l, c, times)
    pip = 0.01 if inst.endswith("_JPY") else 0.0001
    spread = SPREAD_PIPS[inst] * pip
    day = [t[:10] for t in times]

    pos = None  # dict(side, entry, sl, tp, i_open, sig)
    trades = []
    for i in range(len(c) - 1):
        if pos is None:
            sig = strat.signal(st, i)
            if sig:
                entry = o[i + 1] + (spread if sig["side"] == "long" else -spread)
                pos = {"side": sig["side"], "entry": entry, "sl": sig["sl"],
                       "tp": sig.get("tp"), "i_open": i + 1, "sig": sig}
            continue
        j = i  # 保有中バー
        side, sl, tp = pos["side"], pos["sl"], pos["tp"]
        exit_p = None
        reason = None
        if side == "long":
            if l[j] <= sl:
                exit_p, reason = sl, "sl"
            elif tp is not None and h[j] >= tp:
                exit_p, reason = tp, "tp"
        else:
            if h[j] >= sl:
                exit_p, reason = sl, "sl"
            elif tp is not None and l[j] <= tp:
                exit_p, reason = tp, "tp"
        sig = pos["sig"]
        if exit_p is None:
            if sig.get("flip_exit") and strat.flip(st, j, side):
                exit_p, reason = c[j], "flip"
            elif sig.get("rsi_exit") and strat.rsi_exit_hit(st, j, side):
                exit_p, reason = c[j], "rsi"
            elif sig.get("chan_exit") and strat.chan_exit_hit(st, j, side):
                exit_p, reason = c[j], "chan"
            elif sig.get("mid_exit") and strat.mid_exit_hit(st, j, side):
                exit_p, reason = c[j], "mid"
            elif sig.get("color_exit") and strat.color_exit_hit(st, j, side):
                exit_p, reason = c[j], "color"
            elif sig.get("eod") and j + 1 < len(day) and day[j + 1] != day[j]:
                exit_p, reason = c[j], "eod"
            elif sig.get("max_hold") and j - pos["i_open"] >= sig["max_hold"]:
                exit_p, reason = c[j], "hold"
        if exit_p is not None:
            uj = usdjpy_map.get(times[j], 150.0)
            gain = pnl_jpy(inst, side, UNITS, pos["entry"], exit_p, uj)
            trades.append((gain, reason))
            pos = None
    return trades


def metrics(trades):
    if not trades:
        return {"trades": 0}
    pnl = [t[0] for t in trades]
    wins = [p for p in pnl if p > 0]
    losses = [-p for p in pnl if p < 0]
    eq = np.cumsum(pnl)
    dd = float((np.maximum.accumulate(eq) - eq).max())
    return {"trades": len(pnl), "wr": round(len(wins) / len(pnl), 3),
            "pnl": round(float(sum(pnl))),
            "pf": round(sum(wins) / sum(losses), 2) if losses else float("inf"),
            "avg": round(float(np.mean(pnl)), 1), "maxdd": round(dd)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=40000, help="M15本数(40000≈1.6年)")
    args = ap.parse_args()
    s = load_settings()
    client = OandaClient(s)

    hist = {}
    for inst in INSTRUMENTS:
        hist[inst] = load_candles(client, inst, args.bars)
        print("data: %s %d bars %s -> %s" % (
            inst, len(hist[inst]), hist[inst][0][0][:10], hist[inst][-1][0][:10]))

    usdjpy_map = {r[0]: r[4] for r in hist["USD_JPY"]}

    results = {}
    for strat in STRATEGIES:
        per_inst = {}
        all_trades = []
        for inst in INSTRUMENTS:
            tr = simulate(strat, inst, hist[inst], usdjpy_map)
            per_inst[inst] = metrics(tr)
            all_trades += tr
        results[strat.name] = {"total": metrics(all_trades), "per_inst": per_inst}
        t = results[strat.name]["total"]
        print("%-18s trades=%-5s wr=%-6s pnl=%-8s pf=%-6s avg=%-7s maxdd=%s" % (
            strat.name, t.get("trades"), t.get("wr"), t.get("pnl"),
            t.get("pf"), t.get("avg"), t.get("maxdd")))

    out = os.path.join(CACHE_DIR, "classic_results.json")
    with open(out, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print("saved:", out)


if __name__ == "__main__":
    main()
