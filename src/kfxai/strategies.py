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
        decision = str(result.get("decision") or result.get("signal") or "hold").lower()
        confidence = float(result.get("confidence") or 0)
        if confidence > 1:  # 0-100スケールで返る実装もある
            confidence /= 100.0
        pip = pip_size_of(instrument)
        last = candles[-1].close
        reason = str(result.get("reasoning") or result.get("reason") or "")[:120]
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


REGISTRY = {
    "session": SessionBreakout,
    "donchian": DonchianBreakout,
    "rsi_meanrev": RsiMeanReversion,
    "ma_cross": MaCross,
    "llm_analyst": LlmAnalyst,
}


def build_strategies(settings: Settings) -> list:
    names = [n.strip() for n in os.environ.get(
        "KFXAI_ARENA_STRATEGIES", "session,donchian,rsi_meanrev,ma_cross").split(",") if n.strip()]
    out = []
    for n in names:
        cls = REGISTRY.get(n)
        if cls is None:
            print(f"[arena] unknown strategy '{n}' skipped")
            continue
        strategy = cls()
        if hasattr(strategy, "available") and not strategy.available():
            print(f"[arena] {n} disabled (dependency unavailable)")
            continue
        out.append(strategy)
    return out
