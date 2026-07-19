#!/usr/bin/env python3
"""FXデイリーブログ(kfxbrain判断)を Kurageブログ(Bludit)へ1日1回投稿する。

kfxai経由でOANDAの主要FXペアの実データ(価格/騰落/テクニカル)を集め、
kfxbrain(Kurage FX Brain, :18326)の /v1/market/opportunity-ranking に投げて
日本語の機会ランキングを得て、読みやすい記事に整形。カテゴリ/タグ kfxbrain・FX で投稿。
末尾に KAIC(kaic.php)への誘導を付ける。暗号資産版(daily_crypto_blog.py)と対。

  --dry-run  投稿せず本文を標準出力に出すだけ(品質確認用)
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.request
import json
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from kfxai.config import load_settings  # noqa: E402
from kfxai.oanda import OandaClient  # noqa: E402

JST = timezone(timedelta(hours=9))
PAIRS = ["USD_JPY", "EUR_USD", "GBP_JPY", "EUR_JPY", "GBP_USD", "AUD_USD"]
KFXBRAIN_URL = os.environ.get("KFXBRAIN_URL", "http://127.0.0.1:18326")
KFXBRAIN_ENV = "/home/kojima/work/kfxbrain/.env"

CATEGORY = "kfxbrain"
TAGS = "kfxbrain,FX,AI判断"
KAIC_URL = "https://kurage.exbridge.jp/kaic.php"

DISCLOSURE = (
    "\n\n---\n\n"
    "**この記事について**: 上記は [Kurage FX Brain (kfxbrain)](https://kfxbrain.exbridge.jp) が、"
    "各通貨ペアの価格・騰落・テクニカルといった実データだけを根拠に出した構造化判断です。"
    "人気FX関連オープンソースのLLM判断部分を、ローカルLLM(Gemma 4)でAPI化しています。"
    "ブローカー資格情報や注文執行は持ちません。**投資助言ではありません。** "
    "FXはレバレッジにより元本を超える損失が生じる可能性があります。最終判断はご自身で行ってください。\n\n"
    f"🧭 **今日のAI投資委員会の3つの判断（暗号資産・FX・Polymarket）と、7日後の答え合わせは → [Kurage AI Investment Committee (KAIC)]({KAIC_URL})**\n\n"
    "関連: [kfxbrain ワークベンチ](https://kurage.exbridge.jp/kfxbrain.php) / "
    "[Kurage FX AI Trade](https://kurage.exbridge.jp/kfxai.php) / "
    "[Kurage Crypto Brain](https://kurage.exbridge.jp/kcbrain.php)"
)

DIR_JP = {"base_currency": "強気(基軸通貨買い)", "quote_currency": "弱気(基軸通貨売り)",
          "long": "強気(ロング候補)", "short": "弱気(ショート候補)",
          "watch": "様子見", "avoid": "回避"}


def _kfxbrain_token() -> str:
    t = os.environ.get("KFXBRAIN_API_TOKEN")
    if t:
        return t
    with open(KFXBRAIN_ENV, encoding="utf-8") as f:
        for line in f:
            if line.startswith("KFXBRAIN_API_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("KFXBRAIN_API_TOKEN not found")


def gather_evidence() -> list[dict]:
    """OANDAのM15足から主要ペアの実データ証拠を作る。"""
    settings = load_settings()
    client = OandaClient(settings)
    pairs = []
    for inst in PAIRS:
        try:
            candles = [c for c in client.candles(inst, "M15", 240) if c.complete]
            if len(candles) < 100:
                continue
            closes = [c.close for c in candles]
            price = closes[-1]
            pip = 0.01 if inst.endswith("_JPY") else 0.0001
            sma20 = sum(closes[-20:]) / 20
            sma50 = sum(closes[-50:]) / 50
            high24 = max(c.high for c in candles[-96:])
            low24 = min(c.low for c in candles[-96:])
            pairs.append({
                "pair": inst,
                "market": {
                    "price": round(price, 5),
                    "change_4h_pct": round((closes[-1] / closes[-16] - 1) * 100, 2),
                    "change_24h_pct": round((closes[-1] / closes[-96] - 1) * 100, 2),
                    "high_24h": round(high24, 5), "low_24h": round(low24, 5),
                },
                "technicals": {
                    "sma20": round(sma20, 5), "sma50": round(sma50, 5),
                    "price_vs_sma20_pips": round((price - sma20) / pip, 1),
                    "range_position": round((price - low24) / (high24 - low24), 2) if high24 > low24 else None,
                },
            })
        except Exception as exc:
            print(f"[fx-blog] {inst} 取得失敗: {str(exc)[:80]}")
    return pairs


def call_kfxbrain(pairs: list[dict]) -> dict:
    body = json.dumps({
        "timeframe": "H1",
        "as_of": datetime.now(JST).isoformat(timespec="seconds"),
        "pairs": pairs,
        "question": "今後4〜24時間の観点でこれらの主要FXペアを機会順にランキングしてください。"
                    "各ペアのdrivers(根拠)とrisks(リスク)、market_summaryはすべて日本語で、"
                    "1ペアあたり2項目以内の簡潔な日本語で書いてください。"
                    "JSON構造・キー名・symbol(pair)・direction は英語のまま。",
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{KFXBRAIN_URL}/v1/market/opportunity-ranking", data=body,
        headers={"Content-Type": "application/json", "X-KFXBRAIN-Token": _kfxbrain_token()},
        method="POST")
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_article(result: dict) -> tuple[str, str, str]:
    now = datetime.now(JST)
    date_s = now.strftime("%Y年%-m月%-d日")
    r = result.get("result", {})
    rank = r.get("ranking") or []
    summary = r.get("market_summary") or ""
    top = rank[0] if rank else None

    if top:
        title = f"{date_s}のFX AI判断：kfxbrainの本命は{top.get('pair','')}（{DIR_JP.get(top.get('direction'),'—')}）"
    else:
        title = f"{date_s}のFX AI判断（kfxbrain）"

    lines = [f"# {title}", ""]
    lines.append(
        f"Kurage FX Brain（kfxbrain）が{now.strftime('%-m月%-d日 %H時')}時点のOANDA実勢データ"
        "（価格・騰落・テクニカル）だけを根拠に、主要FXペアの機会を判定しました。"
        "以下はローカルLLMが出した構造化判断をそのまま整理したものです。\n")

    if summary:
        lines.append("## 今日の地合い（kfxbrainの要約）\n")
        lines.append(f"> {summary}\n")

    if rank:
        lines.append("## 主要ペアの機会ランキング\n")
        lines.append("| 順位 | 通貨ペア | 方向 | スコア | 主な根拠 |")
        lines.append("|---:|---|---|---:|---|")
        for e in rank:
            drv = "、".join(e.get("drivers", [])[:2]) or "—"
            lines.append(
                f"| {e.get('rank')} | {e.get('pair')} | "
                f"{DIR_JP.get(e.get('direction'),'—')} | {e.get('score')} | {drv} |")
        lines.append("")
        top_risks = (top.get("risks") or []) if top else []
        if top_risks:
            lines.append(
                f"本命の{top.get('pair')}について、kfxbrainは次のリスクも挙げています："
                + "、".join(top_risks[:3]) + "。\n")

    lines.append("## この判断の読み方\n")
    lines.append(
        "スコアはあくまで「与えられた実データの範囲で、リスク調整後にどのペアが相対的に機会があるか」の"
        "順位づけで、将来のレートを保証するものではありません。FXは経済指標や要人発言で急変します。"
        "ここに出ていない材料（当日の指標発表・地政学）も必ず確認し、ポジションサイズを抑えてください。\n")

    body = "\n".join(lines) + DISCLOSURE
    slug = f"fx-kfxbrain-{now.strftime('%Y%m%d')}"
    return title, slug, body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pairs = gather_evidence()
    if len(pairs) < 3:
        print(f"[fx-blog] 証拠が不足({len(pairs)}ペア)。投稿を中止。")
        return 1
    result = call_kfxbrain(pairs)
    title, slug, body = build_article(result)

    if args.dry_run:
        print(f"# [DRY-RUN] title: {title}\n# slug: {slug}\n# category: {CATEGORY} tags: {TAGS}\n")
        print(body)
        return 0

    import post_blog  # noqa: PLC0415
    _, permalink = post_blog.post_to_bludit(title, slug, body, tags=TAGS, category=CATEGORY,
                                            footer="")
    print(f"posted: {permalink}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
