#!/usr/bin/env python3
"""kfxai記事をKurageブログ(kurage.exbridge.jp/blog/ = Bludit)へ投稿する。

kfreqaiと同じBluditを共用し、タグで分離する方針(2026-07-17決定):
  - kfxai記事のタグ: FX自動取引,kfxai
  - kfxai専用ビュー: https://kurage.exbridge.jp/blog/tag/kfxai
認証情報・OGP生成はkfreqai側の実装(blog-bludit-admin.txt / blog_ogp.py)を共用する。

使い方:
  python3 scripts/post_blog.py --title "タイトル" --slug my-slug --file article.md
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

KFREQAI_ADVISORY = "/home/kojima/work/kfreqai/kurage-advisory"
BLUDIT_CREDS_PATH = "/home/kojima/work/kfreqai/user_data/blog-bludit-admin.txt"
BLUDIT_BASE = os.environ.get("KFXAI_BLUDIT_BASE", "https://kurage.exbridge.jp/blog")
JST = timezone(timedelta(hours=9))
TAGS = "FX自動取引,kfxai"

DISCLOSURE_FOOTER = (
    "\n\n---\n\n"
    "**注記**: kfxaiは現在ペーパートレード(OANDAデモ環境の実勢価格に対する仮想取引)で稼働しており、"
    "実際の資金は一切動いていません。本記事の損益・取引はすべてシミュレーション上の数値です。"
    "FXはレバレッジにより元本を超える損失が発生する可能性があります。本記事は投資助言ではありません。"
    "[kfxaiダッシュボード](https://kurage.exbridge.jp/kfxai.php) / "
    "[紹介サイト](https://kfxai.exbridge.jp/kfxai.html)"
)


def load_bludit_creds() -> dict:
    creds = {}
    with open(BLUDIT_CREDS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "=" not in line:
                continue
            k, v = line.split("=", 1)
            creds[k] = v
    return creds


def upload_ogp_image(unique_slug: str, title: str) -> str | None:
    """kfreqaiのblog_ogpでOGP画像を生成しuploadsへFTP格納。失敗しても投稿続行。"""
    try:
        import ftplib
        import io as _io

        sys.path.insert(0, KFREQAI_ADVISORY)
        import blog_ogp  # noqa: PLC0415

        png = blog_ogp.generate(title)
        env = {k: os.environ.get(k, "") for k in ("FTP_HOST", "FTP_USER", "FTP_PASS")}
        if not all(env.values()):
            print("[blog] OGP: FTP認証情報なし、デフォルト画像のまま")
            return None
        filename = f"ogp-{unique_slug}.png"
        with ftplib.FTP(env["FTP_HOST"], timeout=60) as ftp:
            ftp.login(env["FTP_USER"], env["FTP_PASS"])
            ftp.storbinary(
                f"STOR /web/kurage_exbridge_jp/blog/bl-content/uploads/{filename}",
                _io.BytesIO(png))
        return f"{BLUDIT_BASE}/bl-content/uploads/{filename}"
    except Exception as exc:
        print(f"[blog] OGP生成/アップロード失敗(投稿は続行): {str(exc)[:120]}")
        return None


def post_to_bludit(title: str, slug: str, body: str) -> tuple[str, str]:
    creds = load_bludit_creds()
    now = datetime.now(JST)
    unique_slug = f"{slug}-{now.strftime('%Y%m%d-%H%M')}"
    payload = {
        "token": creds["BLUDIT_API_TOKEN"],
        "authentication": creds["BLUDIT_AUTH_TOKEN"],
        "title": title,
        "slug": unique_slug,
        "content": body,
        "status": "published",
        "tags": TAGS,
    }
    cover = upload_ogp_image(unique_slug, title)
    if cover:
        payload["coverImage"] = cover
    resp = requests.post(f"{BLUDIT_BASE}/api/pages", data=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    permalink = data.get("data", {}).get("permalink", f"{BLUDIT_BASE}/{unique_slug}")
    try:
        sys.path.insert(0, KFREQAI_ADVISORY)
        from blog_post import update_blog_sitemap  # noqa: PLC0415
        update_blog_sitemap()
    except Exception as exc:
        print(f"[blog] sitemap更新失敗(投稿は成功): {str(exc)[:120]}")
    return unique_slug, permalink


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--file", required=True, help="本文markdownファイル")
    args = ap.parse_args()

    if not os.path.isfile(args.file):
        print(f"エラー: 本文ファイルが見つかりません: {args.file}")
        print("先に記事本文をmarkdownで書いて、--file にそのパスを渡してください。")
        print('例: python3 scripts/post_blog.py --title "初取引の結果" --slug first-trades --file /tmp/article.md')
        return 1
    with open(args.file, encoding="utf-8") as f:
        body = f.read().strip()
    body += DISCLOSURE_FOOTER

    unique_slug, permalink = post_to_bludit(args.title, args.slug, body)
    print(f"posted: {permalink}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
