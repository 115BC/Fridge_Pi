#!/usr/bin/env bash
# 云端 MySQL 每日冷备：拉全表 JSON 存到派 SD 卡，保留最近 14 份。
# 安装（在派的 ~/fridge_pi 仓库根目录）：
#   chmod +x scripts/cloud_backup.sh
#   (crontab -l 2>/dev/null; echo "17 4 * * * $HOME/fridge_pi/scripts/cloud_backup.sh") | crontab -
set -u
REPO="$HOME/fridge_pi"
DIR="$HOME/fridge_pi_backups"
URL="https://flask-pfwx-325179-5-1301236491.sh.run.tcloudbase.com/api/v1/admin/backup-rows"
mkdir -p "$DIR"
SECRET=$(python3 -c "import yaml;print(yaml.safe_load(open('$REPO/pi/config.yaml'))['security']['secret'])")
OUT="$DIR/fridge-$(date +%F).json"
if curl -fsS --max-time 120 -H "X-Pi-Secret: $SECRET" "$URL" -o "$OUT.tmp"; then
  mv -f "$OUT.tmp" "$OUT"
  ls -1t "$DIR"/fridge-*.json 2>/dev/null | tail -n +15 | xargs -r rm -f
else
  rm -f "$OUT.tmp"
  echo "$(date '+%F %T') 备份失败" >> "$DIR/backup.err"
fi
