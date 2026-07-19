#!/usr/bin/env python3
"""kfxai記事をKurageブログ(kurage.exbridge.jp/blog/ = Bludit)へ手動投稿するCLI。

投稿の実処理・認証・OGP・sitemapは Kurageブログの持ち主である
kfreqai/kurage-advisory/kurage_blog に集約されている。kfxaiはそれを一方向で使う
(2026-07-17『kfxaiはkfreqaiのブログ基盤を共用』方針。循環依存はしない)。

使い方:
  python3 scripts/post_blog.py --title "タイトル" --slug my-slug --file article.md
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, "/home/kojima/work/kfreqai/kurage-advisory")
import kurage_blog  # noqa: E402

TAGS = "FX自動取引,kfxai"
DISCLOSURE_FOOTER = (
    "\n\n---\n\n"
    "**注記**: kfxaiは現在ペーパートレード(OANDAデモ環境の実勢価格に対する仮想取引)で稼働しており、"
    "実際の資金は一切動いていません。本記事の損益・取引はすべてシミュレーション上の数値です。"
    "FXはレバレッジにより元本を超える損失が発生する可能性があります。本記事は投資助言ではありません。"
    "[kfxaiダッシュボード](https://kurage.exbridge.jp/kfxai.php) / "
    "[紹介サイト](https://kfxai.exbridge.jp/kfxai.html)"
)


def post_to_bludit(title: str, slug: str, body: str,
                   tags: str = TAGS, category: str | None = None,
                   footer: str = DISCLOSURE_FOOTER) -> tuple[str, str]:
    """後方互換の薄いラッパー(kfxaiの既定タグ・免責を付けてkurage_blogへ委譲)。"""
    return kurage_blog.post_to_bludit(title, slug, body, tags=tags,
                                      category=category, footer=footer)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--file", required=True, help="本文markdownファイル")
    args = ap.parse_args()

    if not os.path.isfile(args.file):
        print(f"エラー: 本文ファイルが見つかりません: {args.file}")
        print('例: python3 scripts/post_blog.py --title "初取引の結果" --slug first-trades --file /tmp/article.md')
        return 1
    with open(args.file, encoding="utf-8") as f:
        body = f.read().strip()

    _, permalink = post_to_bludit(args.title, args.slug, body)
    print(f"posted: {permalink}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
