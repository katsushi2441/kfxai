#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
set -a
. /home/kojima/work/aixec/.env
set +a

remote="/web/kurage_exbridge_jp"
curl --fail --ftp-create-dirs -T public/kfxai.php \
  "ftp://${FTP_USER}:${FTP_PASS}@${FTP_HOST}${remote}/kfxai.php"
echo "deployed: https://kurage.exbridge.jp/kfxai.php"
