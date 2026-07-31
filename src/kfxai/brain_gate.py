"""Kurage FX Brain(Bankr x402の有料API)によるエントリー選別ゲート。

kfreqai/kfreqaihlが持つ「AIによるエントリー選別」がkfxaiには無く、ルールの
ブレイクアウト条件だけで entry していたのが勝率低下の一因(2026-07-29分析)。
kfreqaihlと同じ設計で組み込む:
  - サイクル中に非holdシグナルが出たときだけ、対象5ペアの証拠をまとめて1回
    kfxbrainの opportunity-ranking に渡し、ペアごとの可否ゲートを作る
  - veto("avoid") または シグナルと逆方向の判断 ならエントリー見送り
  - ゲート取得失敗・タイムアウト・ペア情報なしは fail-open(通す)=障害を機会損失にしない
  - 判断APIはKurageの有料サービス。**Bankr x402(fxbrain)経由で呼び出しごとに自動支払い**する
    (KURAGE_X402_WALLET_KEY のウォレットにBase USDCが必要)。無料の直叩き経路は持たない
"""
from __future__ import annotations

import json
import os

from . import x402_pay

GATE_TIMEOUT = int(os.environ.get("KFXAI_BRAIN_GATE_TIMEOUT", "300"))
_AVOID = {"avoid"}


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2.0 / (period + 1)
    ema = values[0]
    for v in values[1:]:
        ema = v * k + ema * (1 - k)
    return ema


def _rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(-period, 0):
        d = values[i] - values[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100.0 - 100.0 / (1.0 + rs)


def build_pair_evidence(instrument: str, candles: list) -> dict:
    """kfxaiのCandle列から、kfxbrainに渡す証拠1件を作る(hl_brain_clientと同じ形)。"""
    closes = [float(c.close) for c in candles]
    close = closes[-1]
    # M15なので96本=24時間前
    prev24 = closes[-97] if len(closes) >= 97 else closes[0]
    chg24 = (close - prev24) / prev24 * 100.0 if prev24 else 0.0
    tech = {"price": round(close, 6), "change_24h_pct": round(chg24, 2),
            "ema_fast": round(_ema(closes[-120:], 12), 6),
            "ema_slow": round(_ema(closes[-120:], 26), 6)}
    rsi = _rsi(closes)
    if rsi is not None:
        tech["rsi"] = round(rsi, 1)
    return {"pair": instrument, "technicals": tech,
            "market": {"last_price": round(close, 6)}}


def market_gate(pairs_evidence: list[dict]) -> dict:
    """opportunity-rankingを1回呼び、ペアごとの可否ゲートを作る。
    返り値: {"USD_JPY": {"direction": ..., "veto": bool, "why": str}, ...}。
    失敗時は例外(呼び出し側でfail-open)。"""
    payload = {"timeframe": "M15", "pairs": pairs_evidence[:10]}
    # Kurage FX Brain は有料API: Bankr x402で呼び出しごとに自動支払い
    rank = x402_pay.pay_and_call("fxbrain", "/market/opportunity-ranking",
                                 payload, timeout=GATE_TIMEOUT)
    gate: dict = {}
    for r in ((rank.get("result") or {}).get("ranking") or []):
        if not isinstance(r, dict):
            continue  # LLM出力ゆれは無視
        pair = str(r.get("pair") or r.get("symbol") or "").upper()
        if not pair:
            continue
        direction = str(r.get("direction", "watch")).lower()
        gate[pair] = {"direction": direction, "veto": direction in _AVOID,
                      "why": (r.get("drivers") or [""])[0]}
    return gate


def entry_allowed(gate: dict, instrument: str, side: str) -> tuple[bool, str]:
    """kfreqaihlのentry_allowedと同じ規則: 情報なし=通す(fail-open)、
    avoid=見送り、逆方向の判断=見送り。watch/同方向=通す。"""
    g = gate.get(str(instrument).upper())
    if not g:
        return True, "no gate info (fail-open)"
    if g.get("veto"):
        return False, "kfxbrain:avoid %s" % (g.get("why") or "")
    d = g.get("direction")
    if side == "long" and d == "short":
        return False, "kfxbrain:短期は下方向の判断"
    if side == "short" and d == "long":
        return False, "kfxbrain:短期は上方向の判断"
    return True, "kfxbrain:%s" % d
