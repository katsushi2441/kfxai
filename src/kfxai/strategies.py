"""戦略アリーナ: 複数戦略を同一価格フィードで並走させ、戦略別台帳で競わせる。

方針(2026-07-17):
- 1個ずつ順番に試すのではなく、paper上で複数戦略を平行フォワードテストする。
- 戦略はOSSで定番の型(Turtle/Donchian、RSI逆張り、MAクロス)+検証済みのセッション
  ブレイクアウト+LLMアナリスト(kfxbrain=vendored TradingAgents系)をロースターにする。
- 各戦略は自分の建玉・成績だけを持ち、互いに干渉しない(枠は戦略ごとにmax_positions)。
- 注意: 並走比較は「多重比較」なので、成績が良く見えた戦略も
  最低100取引・2ヶ月のフォワード実績が貯まるまでpractice昇格の判断をしない。

戦略の契約:
  name            台帳・決定ログに記録される識別子
  signal(...)     エントリー判定(Signal)。holdなら見送り
  daily_limit     Trueなら1銘柄1日1取引
  close_on_session_end  Trueなら翌朝6時JST(21 UTC)で強制手仕舞い
  max_hold_minutes      Noneでなければ保有時間の上限
"""
from __future__ import annotations

import json
import os
import time as _time
from datetime import datetime

import requests

from .config import Settings
from .models import Candle, Signal
from .indicators import rsi
from .strategy_session import pip_size_of, session_signal


def _hold(instrument: str, name: str, reason: str) -> Signal:
    return Signal(
        instrument=instrument, action="hold", probability_up=0.5, confidence=0.0,
        regime="arena", directive="neutral", reason=reason,
        model=name, features={},
    )


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _atr(candles: list[Candle], period: int = 14) -> float:
    trs = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        trs.append(max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)))
    if len(trs) < period:
        return 0.0
    return sum(trs[-period:]) / period


class SessionBreakout:
    """検証済み(1.6年570取引・全パラメータ黒字)。東京レンジ→夕方ブレイク順張り。"""
    name = "session"
    daily_limit = True
    close_on_session_end = True
    max_hold_minutes = None
    # 東京レンジ→ロンドンブレイクの構造は円ペア前提。非円ペア拡張後も対象を固定する
    instruments = ("USD_JPY", "EUR_JPY", "GBP_JPY")

    def signal(self, instrument: str, candles: list[Candle], settings: Settings,
               now: datetime, already_open: bool) -> Signal:
        if already_open:
            return _hold(instrument, self.name, "position already open")
        sig = session_signal(instrument, candles, settings, now, already_traded_today=False)
        return Signal(**{**sig.__dict__, "model": self.name})


class DonchianBreakout:
    """Turtle系(OSSで最も再実装されている型)。直近24時間の高安ブレイクにATRストップ。"""
    name = "donchian"
    daily_limit = False
    close_on_session_end = False
    max_hold_minutes = 24 * 60
    WINDOW = 96  # M15×96=24時間

    def signal(self, instrument, candles, settings, now, already_open) -> Signal:
        if already_open:
            return _hold(instrument, self.name, "position already open")
        if len(candles) < self.WINDOW + 2:
            return _hold(instrument, self.name, "not enough candles")
        window = candles[-(self.WINDOW + 1):-1]
        hi = max(c.high for c in window)
        lo = min(c.low for c in window)
        last = candles[-1]
        atr = _atr(candles)
        if atr <= 0:
            return _hold(instrument, self.name, "atr unavailable")
        feats = {"channel_high": hi, "channel_low": lo, "atr": atr}
        if last.close > hi:
            return Signal(instrument=instrument, action="buy", probability_up=1.0, confidence=1.0,
                          regime="arena", directive="neutral",
                          reason=f"close broke {self.WINDOW}-bar high {hi:.3f}",
                          model=self.name, features=feats,
                          stop_price=last.close - 2 * atr, take_price=last.close + 3 * atr)
        if last.close < lo:
            return Signal(instrument=instrument, action="sell", probability_up=0.0, confidence=1.0,
                          regime="arena", directive="neutral",
                          reason=f"close broke {self.WINDOW}-bar low {lo:.3f}",
                          model=self.name, features=feats,
                          stop_price=last.close + 2 * atr, take_price=last.close - 3 * atr)
        return _hold(instrument, self.name, "inside channel")


class RsiMeanReversion:
    """RSI逆張り(freqtrade等のOSS戦略で定番の型)。行き過ぎの戻りを取る。"""
    name = "rsi_meanrev"
    daily_limit = False
    close_on_session_end = False
    max_hold_minutes = 8 * 60
    LOW, HIGH = 28.0, 72.0
    SL_PIPS, TP_PIPS = 20.0, 25.0

    def signal(self, instrument, candles, settings, now, already_open) -> Signal:
        if already_open:
            return _hold(instrument, self.name, "position already open")
        closes = [c.close for c in candles]
        value = rsi(closes)
        pip = pip_size_of(instrument)
        last = candles[-1].close
        feats = {"rsi": value}
        if value <= self.LOW:
            return Signal(instrument=instrument, action="buy", probability_up=1.0, confidence=1.0,
                          regime="arena", directive="neutral",
                          reason=f"RSI {value:.1f} oversold",
                          model=self.name, features=feats,
                          stop_price=last - self.SL_PIPS * pip, take_price=last + self.TP_PIPS * pip)
        if value >= self.HIGH:
            return Signal(instrument=instrument, action="sell", probability_up=0.0, confidence=1.0,
                          regime="arena", directive="neutral",
                          reason=f"RSI {value:.1f} overbought",
                          model=self.name, features=feats,
                          stop_price=last + self.SL_PIPS * pip, take_price=last - self.TP_PIPS * pip)
        return _hold(instrument, self.name, f"RSI {value:.1f} neutral")


class MaCross:
    """EMAクロスの順張り(最古典)。直近1本でクロスした瞬間だけ入る。"""
    name = "ma_cross"
    daily_limit = False
    close_on_session_end = False
    max_hold_minutes = 24 * 60
    FAST, SLOW = 20, 80
    SL_PIPS, TP_PIPS = 25.0, 40.0

    def signal(self, instrument, candles, settings, now, already_open) -> Signal:
        if already_open:
            return _hold(instrument, self.name, "position already open")
        closes = [c.close for c in candles]
        if len(closes) < self.SLOW + 2:
            return _hold(instrument, self.name, "not enough candles")
        fast = _ema(closes, self.FAST)
        slow = _ema(closes, self.SLOW)
        pip = pip_size_of(instrument)
        last = closes[-1]
        feats = {"ema_fast": fast[-1], "ema_slow": slow[-1]}
        crossed_up = fast[-2] <= slow[-2] and fast[-1] > slow[-1]
        crossed_dn = fast[-2] >= slow[-2] and fast[-1] < slow[-1]
        if crossed_up:
            return Signal(instrument=instrument, action="buy", probability_up=1.0, confidence=1.0,
                          regime="arena", directive="neutral",
                          reason=f"EMA{self.FAST} crossed above EMA{self.SLOW}",
                          model=self.name, features=feats,
                          stop_price=last - self.SL_PIPS * pip, take_price=last + self.TP_PIPS * pip)
        if crossed_dn:
            return Signal(instrument=instrument, action="sell", probability_up=0.0, confidence=1.0,
                          regime="arena", directive="neutral",
                          reason=f"EMA{self.FAST} crossed below EMA{self.SLOW}",
                          model=self.name, features=feats,
                          stop_price=last + self.SL_PIPS * pip, take_price=last - self.TP_PIPS * pip)
        return _hold(instrument, self.name, "no fresh cross")


class LlmAnalyst:
    """LLMアナリスト戦略: kfxbrain(vendored TradingAgents/FinGPT系のGemma 4判断API)の
    /v1/decide/trade にテクニカル要約を渡し、BUY/SELL/HOLDに従う。
    判断は1銘柄1時間キャッシュ(GPUを叩きすぎない)。tokenが無ければ自動無効。"""
    name = "llm_analyst"
    daily_limit = False
    close_on_session_end = False
    max_hold_minutes = 24 * 60
    SL_PIPS, TP_PIPS = 25.0, 40.0
    CACHE_SECONDS = 3600

    def __init__(self) -> None:
        self.base = os.environ.get("KFXAI_KFXBRAIN_URL", "http://127.0.0.1:18326").rstrip("/")
        self.token = os.environ.get("KFXAI_KFXBRAIN_TOKEN", "").strip() or self._token_from_file()
        self._cache: dict[str, tuple[float, dict]] = {}

    @staticmethod
    def _token_from_file() -> str:
        # 同居デプロイの既定: kfxbrain/.env からトークンを読む(無ければ無効化)
        try:
            for line in open("/home/kojima/work/kfxbrain/.env", encoding="utf-8"):
                if line.startswith("KFXBRAIN_API_TOKEN="):
                    return line.split("=", 1)[1].strip()
        except OSError:
            pass
        return ""

    def available(self) -> bool:
        return bool(self.token)

    def _judge(self, instrument: str, candles: list[Candle]) -> dict:
        cached = self._cache.get(instrument)
        if cached and _time.time() - cached[0] < self.CACHE_SECONDS:
            return cached[1]
        closes = [c.close for c in candles]
        pip = pip_size_of(instrument)
        payload = {
            "pair": instrument,
            "timeframe": "M15",
            "technicals": {
                "last_close": closes[-1],
                "chg_4h_pct": round((closes[-1] / closes[-16] - 1) * 100, 3) if len(closes) > 16 else 0,
                "chg_24h_pct": round((closes[-1] / closes[-96] - 1) * 100, 3) if len(closes) > 96 else 0,
                "rsi_14": round(rsi(closes), 1),
                "range_24h_pips": round((max(c.high for c in candles[-96:]) - min(c.low for c in candles[-96:])) / pip, 1) if len(candles) >= 96 else 0,
            },
            "question": "Short-term (intraday) direction for a small paper trade. Answer buy, sell, or hold.",
        }
        r = requests.post(
            f"{self.base}/v1/decide/trade",
            headers={"X-KFXBRAIN-Token": self.token, "Content-Type": "application/json"},
            data=json.dumps(payload), timeout=180,
        )
        r.raise_for_status()
        result = r.json().get("result", {})
        self._cache[instrument] = (_time.time(), result)
        return result

    def signal(self, instrument, candles, settings, now, already_open) -> Signal:
        if already_open:
            return _hold(instrument, self.name, "position already open")
        if not self.available():
            return _hold(instrument, self.name, "kfxbrain token unavailable")
        try:
            result = self._judge(instrument, candles)
        except Exception as exc:
            return _hold(instrument, self.name, f"kfxbrain error: {str(exc)[:60]}")
        # kfxbrainの本来のキーは action / rationale。旧実装のdecision/signal・reasoning
        # だけを見ると常にNone→"hold"に落ちてCが永久に取引できなかった(2026-07-21修正)。
        decision = str(result.get("action") or result.get("decision")
                       or result.get("signal") or "hold").lower()
        confidence = float(result.get("confidence") or 0)
        if confidence > 1:  # 0-100スケールで返る実装もある
            confidence /= 100.0
        pip = pip_size_of(instrument)
        last = candles[-1].close
        raw_reason = (result.get("rationale") or result.get("reasoning")
                      or result.get("reason") or "")
        if isinstance(raw_reason, (list, tuple)):
            raw_reason = " / ".join(str(x) for x in raw_reason)
        reason = str(raw_reason)[:120]
        feats = {"llm_confidence": confidence}
        if decision in ("buy", "long") and confidence >= 0.55:
            return Signal(instrument=instrument, action="buy", probability_up=1.0,
                          confidence=confidence, regime="arena", directive="neutral",
                          reason=f"kfxbrain: {reason}", model=self.name, features=feats,
                          stop_price=last - self.SL_PIPS * pip, take_price=last + self.TP_PIPS * pip)
        if decision in ("sell", "short") and confidence >= 0.55:
            return Signal(instrument=instrument, action="sell", probability_up=0.0,
                          confidence=confidence, regime="arena", directive="neutral",
                          reason=f"kfxbrain: {reason}", model=self.name, features=feats,
                          stop_price=last + self.SL_PIPS * pip, take_price=last - self.TP_PIPS * pip)
        return _hold(instrument, self.name, f"kfxbrain: {decision} (conf {confidence:.2f})")


class DualThrust:
    """Dual Thrust(Michael Chalek)。前日レンジ×Kを当日始値に足し引きした水準の
    ブレイクで順張り、当日中(セッション終了21UTC)に手仕舞い。

    2026-07-17 backtest_classic.py(1.6年M15・スプレッド課金)で採用:
    USD_JPY/EUR_USD/GBP_USDはK=0.4〜0.7の全値で黒字(パラメータにロバスト)、
    K=0.5で3ペア合計+26,487円/1,205取引(1000通貨)。円クロス(EUR_JPY/GBP_JPY)と
    AUD_USDは赤字のため対象外。落第したdonchian/rsi_meanrev/ma_cross
    (同基盤で-9.9万/-5.9万/-3.8万)の後継。
    バックテストはUTC日付終わりで手仕舞い、ライブは21:00 UTCクローズ(既存の
    セッション終了機構を再利用)でわずかに早い点だけ差異がある。
    """
    name = "dual_thrust"
    # バックテストはSL後の同日再エントリーを許す(その条件で+26,487)ため1日1回制限はしない
    daily_limit = False
    close_on_session_end = True
    max_hold_minutes = None
    instruments = ("USD_JPY", "EUR_USD", "GBP_USD")
    K = 0.5

    def signal(self, instrument, candles, settings, now, already_open) -> Signal:
        if already_open:
            return _hold(instrument, self.name, "position already open")
        today = now.strftime("%Y-%m-%d")
        prev: list[Candle] = []
        cur: list[Candle] = []
        for c in candles:
            day = c.time[:10]
            if day == today:
                cur.append(c)
            else:
                prev.append((day, c))
        if not cur:
            return _hold(instrument, self.name, "no candles for today yet")
        prev_days = [d for d, _ in prev]
        if not prev_days:
            return _hold(instrument, self.name, "no previous day data")
        last_day = prev_days[-1]
        pd = [c for d, c in prev if d == last_day]
        if len(pd) < 20:
            return _hold(instrument, self.name, "previous day incomplete")
        hh = max(c.high for c in pd)
        ll = min(c.low for c in pd)
        cc = pd[-1].close
        rng = max(hh - min(cc, ll), max(cc, hh) - ll)
        if rng <= 0:
            return _hold(instrument, self.name, "zero range")
        day_open = cur[0].open
        up = day_open + self.K * rng
        dn = day_open - self.K * rng
        last = candles[-1]
        # 21UTC以降は新規なし(セッション終了クローズと同期)
        if now.hour >= 20:
            return _hold(instrument, self.name, "too late in session")
        feats = {"day_open": day_open, "prev_range": rng, "band_up": up, "band_dn": dn}
        if last.close > up:
            return Signal(instrument=instrument, action="buy", probability_up=1.0, confidence=1.0,
                          regime="arena", directive="neutral",
                          reason=f"dual-thrust break above {up:.3f} (K={self.K})",
                          model=self.name, features=feats,
                          stop_price=dn, take_price=last.close + 3 * rng)
        if last.close < dn:
            return Signal(instrument=instrument, action="sell", probability_up=0.0, confidence=1.0,
                          regime="arena", directive="neutral",
                          reason=f"dual-thrust break below {dn:.3f} (K={self.K})",
                          model=self.name, features=feats,
                          stop_price=up, take_price=last.close - 3 * rng)
        return _hold(instrument, self.name, "inside thrust bands")


REGISTRY = {
    "session": SessionBreakout,
    "donchian": DonchianBreakout,
    "rsi_meanrev": RsiMeanReversion,
    "ma_cross": MaCross,
    "llm_analyst": LlmAnalyst,
    "dual_thrust": DualThrust,
}


class Investor:
    """1投資家 = 予算30万・枠3を持つ実験レーン(2026-07-18)。

    本番の直列バックテストが遅すぎるので、本番の横で投資家を並走させ「試行を並列化」
    するのが目的。投資家は別人格が複数のサブ戦略を回す"進化する複合戦略"にすぎず、
    名前(A/B/C)に意味はない・戦略を固定しない(どんどん変わり増える)。どのサブが信号を
    出しても建てる(優先順)。成績は**投資家単位**で評価し、戦略別の帰属分析はしない
    (本番同様、複合的要因で取引しているため切り分け不能)。良い投資家のロジックを本番へ。

    サブ戦略の入れ替え = 私がそのレーンで試行錯誤する行為。env KFXAI_INVESTOR_<X> で
    サブ集合を上書きできる(例: KFXAI_INVESTOR_A="session,dual_thrust")。
    """

    def __init__(self, name: str, subs: list) -> None:
        self.name = name
        self.subs = subs
        # 決済ルールは投資家単位に集約(seedは同質。混在時は保守的側=長く持たせSL/TP任せ)
        self.daily_limit = all(getattr(s, "daily_limit", False) for s in subs)
        self.close_on_session_end = any(getattr(s, "close_on_session_end", False) for s in subs)
        holds = [s.max_hold_minutes for s in subs if getattr(s, "max_hold_minutes", None) is not None]
        self.max_hold_minutes = max(holds) if holds else None
        # 対象ペア: 全サブが制限を持つ時のみ和集合で制限、1つでも無制限なら無制限
        if subs and all(getattr(s, "instruments", None) for s in subs):
            insts: set = set()
            for s in subs:
                insts |= set(s.instruments)
            self.instruments = tuple(sorted(insts))
        else:
            self.instruments = None

    def available(self) -> bool:
        return all(not hasattr(s, "available") or s.available() for s in self.subs)

    def signal(self, instrument, candles, settings, now, already_open) -> Signal:
        if already_open:
            return _hold(instrument, self.name, "position already open")
        # 全サブがholdの時は、最後に評価したサブのhold理由を残す(診断できるように)。
        last_hold = "no sub-strategy signal"
        for sub in self.subs:
            si = getattr(sub, "instruments", None)
            if si and instrument not in si:
                continue
            sig = sub.signal(instrument, candles, settings, now, already_open)
            if sig.action != "hold":
                # model=投資家名(評価は投資家単位)。どのサブ由来かはreasonにだけ残す(表示用)
                return Signal(**{**sig.__dict__, "model": self.name,
                                 "reason": f"[{sub.name}] {sig.reason}"})
            last_hold = f"[{sub.name}] {sig.reason}"
        return _hold(instrument, self.name, last_hold)


# 3投資家レーン。名前(A/B/C)に意味は無く、seedは進化の出発点にすぎない(固定しない)。
INVESTOR_DEFS = [
    ("A", ["session"]),
    ("B", ["dual_thrust"]),
    ("C", ["llm_analyst"]),
]


def _resolve_subs(label: str, subnames: list) -> list:
    subs = []
    for n in subnames:
        cls = REGISTRY.get(n)
        if cls is None:
            print(f"[{label}] unknown strategy '{n}' skipped")
            continue
        s = cls()
        if hasattr(s, "available") and not s.available():
            print(f"[{label}] {n} disabled (dependency unavailable)")
            continue
        subs.append(s)
    return subs


def build_strategies(settings: Settings) -> list:
    """アリーナ3投資家(A/B/C)。名前に意味はなく中身は進化する複合戦略。"""
    out = []
    for name, default_subs in INVESTOR_DEFS:
        subnames = [n.strip() for n in os.environ.get(
            f"KFXAI_INVESTOR_{name}", ",".join(default_subs)).split(",") if n.strip()]
        subs = _resolve_subs(f"arena {name}", subnames)
        if subs:
            out.append(Investor(name, subs))
    return out


def build_production(settings: Settings) -> list:
    """本番レーン(kfreqaiの本番botに相当)。アリーナの成果を昇格していく先。
    現行の本番戦略はseed=session breakout(1.6年検証済み)。中身はenv KFXAI_PRODUCTION で進化。"""
    subnames = [n.strip() for n in os.environ.get("KFXAI_PRODUCTION", "session").split(",") if n.strip()]
    subs = _resolve_subs("本番", subnames)
    return [Investor("本番", subs)] if subs else []
