#!/usr/bin/env bash
# landing/ を heteml(kfxai.exbridge.jp)へデプロイする。
# index.html=英語 / kfxai.html=日本語 / assets=Kurageアバター。
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . /home/kojima/work/aixec/.env; set +a

REMOTE="/web/kfxai_exbridge_jp"
for f in index.html kfxai.html assets/kurage_avatar.webp assets/kurage_avatar.png; do
  curl --fail --ftp-create-dirs -T "landing/$f" "ftp://${FTP_USER}:${FTP_PASS}@${FTP_HOST}${REMOTE}/$f"
  echo "deployed landing/$f"
done
echo "-> https://kfxai.exbridge.jp/"
