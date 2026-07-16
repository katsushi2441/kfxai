"""セッションブレイクアウト戦略(東京レンジ→ロンドンブレイク)。

検証(scripts/backtest_session.py, 2026-07-16):
- 1.6年570取引 tp=1.5 で +11,360 JPY/1000通貨。全パラメータ(tp 0.8-1.5, buf 1-3)で符号プラス。
- エッジは円ペアに集中(東京セッションは円の主戦場でレンジに構造的意味がある)。
  USD_JPY +4,347 / EUR_JPY +5,533 / GBP_JPY +955。AUD_JPYは両窓マイナスで除外、
  EUR_USDは符号不安定(tp1.0で-3k, 1.5で+1.3k)で除外。→ 運用は円3ペア推奨。

ルール:
- 東京レンジ = 00:00-07:00 UTC の高値・安値(M15)
- 07:00-12:00 UTC にレンジ±バッファをclose がブレイクしたら順張り
- SL = レンジ反対側 / TP = entry ± レンジ幅×tp_mult / 21:00 UTC 強制手仕舞い
- レンジ幅が min_range_pips 未満 or max_sl_pips 超の日は見送り
- 1銘柄1日1取引
"""
from __future__ import annotations

from datetime import datetime, timezone

from .config import Settings
from .models import Candle, Signal


def pip_size_of(instrument: str) -> float:
    return 0.01 if instrument.endswith("_JPY") else 0.0001


def _hour(t: str) -> int:
    return int(t[11:13])


def _day(t: str) -> str:
    return t[:10]


def tokyo_range(candles: list[Candle], day: str, settings: Settings) -> tuple[float, float] | None:
    """当日の東京レンジ(高値, 安値)。レンジ時間帯の足が薄い日はNone。"""
    rng = [
        c for c in candles
        if _day(c.time) == day and settings.session_range_start <= _hour(c.time) < settings.session_range_end
    ]
    if len(rng) < 20:
        return None
    return max(c.high for c in rng), min(c.low for c in rng)


def session_signal(
    instrument: str,
    candles: list[Candle],
    settings: Settings,
    now: datetime | None = None,
    already_traded_today: bool = False,
) -> Signal:
    """現時点のセッションブレイクアウト判定。エントリー条件外は action=hold。"""
    now = now or datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    hour = now.hour
    pip = pip_size_of(instrument)

    def hold(reason: str) -> Signal:
        return Signal(
            instrument=instrument, action="hold", probability_up=0.5, confidence=0.0,
            regime="session", directive="neutral", reason=reason,
            model="session-breakout-v1", features={},
        )

    if already_traded_today:
        return hold("already traded today")
    if not (settings.session_range_end <= hour < settings.session_entry_until):
        return hold(f"outside entry window ({settings.session_range_end}-{settings.session_entry_until} UTC)")

    rng = tokyo_range(candles, today, settings)
    if rng is None:
        return hold("tokyo range unavailable")
    hi, lo = rng
    range_pips = (hi - lo) / pip
    if range_pips < settings.session_min_range_pips:
        return hold(f"range too narrow ({range_pips:.1f}p)")
    if range_pips > settings.session_max_sl_pips:
        return hold(f"range too wide ({range_pips:.1f}p)")

    last = candles[-1]
    if _day(last.time) != today:
        return hold("no candle for today yet")
    buf = settings.session_buffer_pips * pip
    height = hi - lo

    features: dict[str, float] = {
        "range_high": hi, "range_low": lo, "range_pips": range_pips,
    }
    if last.close > hi + buf:
        return Signal(
            instrument=instrument, action="buy", probability_up=1.0, confidence=1.0,
            regime="session", directive="neutral",
            reason=f"broke above tokyo range {hi:.3f} (range {range_pips:.1f}p)",
            model="session-breakout-v1", features=features,
            stop_price=lo, take_price=last.close + height * settings.session_tp_mult,
        )
    if last.close < lo - buf:
        return Signal(
            instrument=instrument, action="sell", probability_up=0.0, confidence=1.0,
            regime="session", directive="neutral",
            reason=f"broke below tokyo range {lo:.3f} (range {range_pips:.1f}p)",
            model="session-breakout-v1", features=features,
            stop_price=hi, take_price=last.close - height * settings.session_tp_mult,
        )
    return hold(f"inside tokyo range ({lo:.3f}-{hi:.3f})")


def session_should_close(now: datetime, settings: Settings) -> bool:
    """強制手仕舞い時刻(21:00 UTC以降、または日付跨ぎの早朝=レンジ形成中)か。"""
    return now.hour >= settings.session_close_hour or now.hour < settings.session_range_end
