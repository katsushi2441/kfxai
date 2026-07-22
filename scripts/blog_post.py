#!/usr/bin/env python3
"""kfxai 自動取引ブログの定時投稿(1日3回 05:30 / 13:30 / 21:30 JST)。

kfreqai の kurage-advisory/blog_post.py と同型の、kfxai(FX)版。kfxai自身の
ダッシュボードAPI(:18324 /api/status)から現在のレーン構成・損益・地合い・直近約定を
読み、ローカルLLM(Gemma 4)に短い日本語記事(定時レポート / 21時は日次総括)を書かせて
Kurageブログ(Bludit)の "kfxai" カテゴリへ投稿する。21:30の総括ははてな/Bloggerへも転載。

投稿時間は kfreqai(05/13/21:00)から約30分ずらして 05:30/13:30/21:30 にしている
(同一ブログ基盤への同時集中を避ける)。

Hard rule: kfxai は dry-run(OANDAデモの実勢価格に対する仮想取引)。実資金は動いていない。
免責フッターはコード側(post_blog.DISCLOSURE_FOOTER)で必ず付与し、生成に依存しない。

  --dry-run  投稿せず生成本文を標準出力に出すだけ(品質確認用)
"""
from __future__ import annotations

import argparse
import os
import re
import smtplib
import ssl
import sys
import time
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import post_blog  # noqa: E402  (kfxaiカテゴリ+FX免責でkurage_blogへ委譲する既存ラッパー)

JST = timezone(timedelta(hours=9))

KFXAI_API = os.environ.get("KFXAI_API_BASE", "http://127.0.0.1:18324").rstrip("/")
OLLAMA_URL = os.environ.get("KFXAI_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("KFXAI_OLLAMA_MODEL", "gemma4:12b-it-qat").strip()

DASHBOARD_URL = "https://kurage.exbridge.jp/kfxai.php"


# --------------------------------------------------------------------------
# LLM (Gemma 4, ローカル)
# --------------------------------------------------------------------------
def call_gemma(prompt: str, num_predict: int = 1600, temperature: float = 0.55,
               timeout: int = 300) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "think": False,  # gemma4は思考型: 無効化しないと隠れ推論でnum_predictを食い潰し空応答になる
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    resp = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=timeout)
    resp.raise_for_status()
    text = resp.json().get("response") or ""
    if not text.strip():
        raise RuntimeError("gemma4 returned empty response")
    return text


def parse_blog_response(text: str) -> tuple[str, str, str]:
    title_match = re.search(r"^TITLE:\s*(.+)$", text, re.MULTILINE)
    slug_match = re.search(r"^SLUG:\s*(.+)$", text, re.MULTILINE)
    if not title_match:
        raise RuntimeError("could not parse TITLE from LLM output")
    title = title_match.group(1).strip()[:80]
    raw_slug = slug_match.group(1).strip() if slug_match else ""
    slug = re.sub(r"[^a-z0-9-]", "", raw_slug.lower().replace(" ", "-"))[:60] or "kfxai-update"
    body = text.split("---", 1)[1].strip() if "---" in text else text
    return title, slug, body


# --------------------------------------------------------------------------
# コンテキスト収集(kfxai自身のダッシュボードAPI)
# --------------------------------------------------------------------------
def gather_context() -> dict:
    resp = requests.get(f"{KFXAI_API}/api/status", timeout=15)
    resp.raise_for_status()
    return resp.json()


def _lane_lines(d: dict) -> tuple[str, float]:
    """稼働/停止レーンの一覧テキストと、全レーン累計損益を返す。"""
    lines = []
    total = 0.0
    for r in d.get("strategy_performance", []):
        if not (r.get("production") or r.get("arena")):
            continue  # 旧単体戦略(legacy)はレーン外なので除外
        pnl = float(r.get("pnl_jpy") or 0)
        total += pnl
        if r.get("production"):
            tag = "本番"
        elif r.get("stopped"):
            tag = "停止"
        else:
            tag = "アリーナ"
        subs = "+".join(r.get("subs") or []) or "(停止・会計のみ)"
        wr = f"{round(100 * r['wins'] / r['trades'])}%" if r.get("trades") else "-"
        lines.append(f"[{tag}] {r.get('strategy')}: {subs} — 決済{r.get('trades', 0)}件 "
                     f"勝率{wr} 累計{pnl:+.0f}円")
    return "\n".join(lines) or "（レーンなし）", total


def _trade_lines(d: dict, limit: int = 8) -> str:
    lines = []
    for t in d.get("recent_trades", []):
        if t.get("status") != "closed":
            continue
        pnl = t.get("pnl_jpy")
        when = (t.get("close_time") or "")[:16].replace("T", " ")
        lines.append(f"{when}: {t.get('instrument', '?')} {t.get('side', '')} "
                     f"{'' if pnl is None else f'{pnl:+.0f}円'} ({t.get('exit_reason', '-')})")
        if len(lines) >= limit:
            break
    return "\n".join(lines) or "（直近の決済なし）"


def build_prompt(d: dict, is_daily_summary: bool) -> str:
    regime = d.get("regime", {}) or {}
    directive = d.get("directive", {}) or {}
    lane_block, total_pnl = _lane_lines(d)
    trades_block = _trade_lines(d, limit=12 if is_daily_summary else 6)
    open_n = len(d.get("open_trades", []) or [])
    instruments = ", ".join(d.get("instruments", []) or [])
    kind = "1日の総括（21:30）" if is_daily_summary else "定時の市況チェック（5:30 / 13:30）"

    return f"""あなたはKurageプロジェクトのFX自動取引bot「kfxai」の「中の人」として、ブログ記事を書くAIです。
今回の記事種別: {kind}

# 使えるデータ（これ以外の事実・数値は絶対に創作しない）

対象通貨ペア: {instruments}
AI地合い判定: {regime.get('regime', '不明')} - {regime.get('note', '')}
リスク方針: {directive.get('directive', '不明')} - {directive.get('note', '')}
現在の保有ポジション数: {open_n}

レーン別成績（本番=昇格先 / アリーナ=試行中 / 停止=昇格・退役済みで会計のみ）:
{lane_block}

全レーン累計損益（＝上記レーンの損益合計）: {total_pnl:+.0f}円

直近の決済履歴:
{trades_block}

# kfxaiの仕組み（記事の背景として使ってよい）
- 本番レーンの横で「アリーナ」に複数戦略を並走させ、勝った戦略を本番へ昇格し、負け筋は停止する。
- 停止レーンは新規取引しないが過去の損益は累計に残る。累計損益は全レーンの合計。

# 執筆ルール
- 日本語で、FXに詳しい個人ブロガーのような自然な文体で書く。
- タイトルは40字以内、具体的で興味を引くものにする。
- 本文はMarkdown形式で{'700〜1000字程度' if is_daily_summary else '400〜700字程度'}。
- 上記データにない事実・数値は絶対に創作しない。
- **kfxaiは紙上取引(dry-run)であり実際の資金は動いていないことを本文中でも自然に触れる。**
- 煽り・投資助言・断定的な将来予測はしない（「〜の可能性がある」程度に留める）。
{"- 総括記事なので、今日のレーンの動き・昇格/停止・決済結果を振り返る構成にする。" if is_daily_summary else "- 短めの定時レポートとして、現在の地合いと注目レーン・注目ポイントを簡潔にまとめる。"}

# 出力形式（この見出し以外、余計な文章を書かない）

TITLE: <タイトル>
SLUG: <URLに使う英語スラッグ。小文字・ハイフン区切り・3〜6単語>
---
<Markdown本文>
"""


# --------------------------------------------------------------------------
# はてな / Blogger 転載(21:30の総括のみ)
# --------------------------------------------------------------------------
def send_mail(title: str, body_markdown: str, to_addr: str) -> None:
    smtp_host = os.environ.get("SMTP_HOST", "mail18.heteml.jp")
    smtp_port = int(os.environ.get("SMTP_PORT", 465))
    smtp_from = os.environ["SMTP_FROM"]
    smtp_pass = os.environ["SMTP_PASSWORD"]
    try:
        import markdown
        html_body = markdown.markdown(body_markdown, extensions=["extra"])
    except Exception:
        html_body = "<pre>" + body_markdown.replace("<", "&lt;") + "</pre>"
    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = title
    msg["From"] = smtp_from
    msg["To"] = to_addr
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ctx) as s:
        s.login(smtp_from, smtp_pass)
        s.sendmail(smtp_from, [to_addr], msg.as_bytes())


def crosspost_email(title: str, body_markdown: str, permalink: str) -> None:
    targets = {"hatena": os.environ.get("HATENA_POST_EMAIL", ""),
               "blogger": os.environ.get("BLOGGER_POST_EMAIL", "")}
    backlink = f"\n\n---\n\nより詳しいデータは元記事をどうぞ: [{title}]({permalink})"
    for channel, to_addr in targets.items():
        if not to_addr:
            continue
        try:
            send_mail(title, body_markdown + backlink, to_addr)
            print(f"[kfxai blog] crossposted to {channel}: {title}", flush=True)
        except Exception as exc:
            print(f"[kfxai blog] crosspost {channel} failed: {exc}", flush=True)
        time.sleep(3)


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="投稿せず本文を出力するだけ")
    args = ap.parse_args()

    now = datetime.now(JST)
    is_daily_summary = now.hour >= 18  # 21:30の総括(RandomizedDelaySecの余裕を含める)

    try:
        ctx = gather_context()
    except Exception as exc:
        print(f"[kfxai blog] status取得に失敗、投稿中止: {exc}", flush=True)
        return 1

    prompt = build_prompt(ctx, is_daily_summary)
    try:
        text = call_gemma(prompt, num_predict=2000 if is_daily_summary else 1400)
        title, slug, body = parse_blog_response(text)
    except Exception as exc:
        print(f"[kfxai blog] 記事生成に失敗、投稿中止: {exc}", flush=True)
        return 1

    if args.dry_run:
        print(f"# TITLE: {title}\n# SLUG: {slug}\n# daily_summary={is_daily_summary}\n")
        print(body)
        print("\n---（--dry-run: 未投稿。免責フッターは投稿時に自動付与）---")
        return 0

    try:
        _, permalink = post_blog.post_to_bludit(title, slug, body)
    except Exception as exc:
        print(f"[kfxai blog] Bludit投稿に失敗: {exc}", flush=True)
        return 1
    print(f"[kfxai blog] posted: {title} -> {permalink}", flush=True)

    if is_daily_summary:
        crosspost_email(title, body + post_blog.DISCLOSURE_FOOTER, permalink)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
