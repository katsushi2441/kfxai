"""kfxbrain(:18326) によるエントリー選別ゲート(kfreqaihlのhl_brain_clientと同型)。

kfreqai/kfreqaihlが持つ「AIによるエントリー選別」がkfxaiには無く、ルールの
ブレイクアウト条件だけで entry していたのが勝率低下の一因(2026-07-29分析)。
kfreqaihlと同じ設計で組み込む:
  - サイクル中に非holdシグナルが出たときだけ、対象5ペアの証拠をまとめて1回
    kfxbrainの opportunity-ranking に渡し、ペアごとの可否ゲートを作る
  - veto("avoid") または シグナルと逆方向の判断 ならエントリー見送り
  - ゲート取得失敗・タイムアウト・ペア情報なしは fail-open(通す)=障害を機会損失にしない
  - 自分のbotなのでproviderヘッダ無し=ローカルgemma(無料)。tokenはkfxbrain/.envから読む
"""
from __future__ import annotations

import json
import os
import urllib.request

KFXBRAIN_URL = os.environ.get("KFXBRAIN_URL", "http://127.0.0.1:18326").rstrip("/")
KFXBRAIN_ENV = os.environ.get("KFXBRAIN_ENV_PATH", "/home/kojima/work/kfxbrain/.env")
GATE_TIMEOUT = int(os.environ.get("KFXAI_BRAIN_GATE_TIMEOUT", "120"))
_AVOID = {"avoid"}


def _token() -> str:
    tok = os.environ.get("KFXBRAIN_API_TOKEN", "").strip()
    if tok:
        return tok
    try:
        with open(KFXBRAIN_ENV, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("KFXBRAIN_API_TOKEN="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


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
    req = urllib.request.Request(
        KFXBRAIN_URL + "/v1/market/opportunity-ranking",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-KFXBrain-Token": _token()},
        method="POST")
    with urllib.request.urlopen(req, timeout=GATE_TIMEOUT) as resp:
        rank = json.loads(resp.read().decode("utf-8"))
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
