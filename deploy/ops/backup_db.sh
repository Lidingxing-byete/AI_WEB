#!/usr/bin/env bash
# SQLite 在线备份（用 Python 的 backup API，服务运行中也能安全备份）
# 用法：bash backup_db.sh            # 备份到 /opt/xinzhen/backups，保留最近 14 份
set -euo pipefail

DB=/opt/xinzhen/apps/data/xinzhen.db
DIR=/opt/xinzhen/backups
KEEP=14
PY=/opt/xinzhen/venv/bin/python

mkdir -p "$DIR"
[ -f "$DB" ] || { echo "[skip] 数据库不存在：$DB"; exit 0; }

STAMP=$(date +%Y%m%d-%H%M%S)
OUT="$DIR/xinzhen-$STAMP.db"

"$PY" - "$DB" "$OUT" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
    s.backup(d)          # 在线备份，WAL 下也一致
print(f"backup -> {dst}")
PY

gzip -f "$OUT"
ls -1t "$DIR"/xinzhen-*.db.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
echo "[ok] 备份完成：$OUT.gz（保留最近 $KEEP 份）"
ls -lh "$DIR" | tail -5
