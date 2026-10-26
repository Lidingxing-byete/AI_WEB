#!/usr/bin/env bash
# ============================================================================
# 芯诊 AI · 阿里云服务器一键安装（Alibaba Cloud Linux 3 / RHEL 系）
#
# 作用：装 Python 环境 → 拉取代码 → 建 venv → 装依赖 → 生成 .env →
#       装 systemd 服务并启动 → 把 /xz/ 的 Nginx 配置插入现有站点 → 重载
#
# 用法（在服务器上以 root 执行）：
#   curl -fsSL https://raw.githubusercontent.com/Lidingxing-byete/AI_WEB/main/deploy/install_on_aliyun.sh -o /tmp/xz-install.sh
#   bash /tmp/xz-install.sh
#
# 幂等：重复执行只会做"缺的那部分"；改 Nginx 前会备份，nginx -t 失败自动回滚。
# 不做的事：不装 Node（前端在本地构建后上传）、不申请证书、不改你博客的任何配置。
# ============================================================================
set -euo pipefail

DOMAIN_CONF="${DOMAIN_CONF:-/etc/nginx/conf.d/zerovoidlog.cn.conf}"
APP_DIR="${APP_DIR:-/opt/xinzhen}"
SERVICE_USER="${SERVICE_USER:-xinzhen}"
REPO_URL="${REPO_URL:-https://github.com/Lidingxing-byete/AI_WEB.git}"
PORT="${PORT:-8787}"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m[x] %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" = "0" ] || die "请用 root 执行（sudo bash $0）"
command -v nginx >/dev/null || die "未找到 nginx，请先确认站点已在运行"
[ -f "$DOMAIN_CONF" ] || die "找不到 $DOMAIN_CONF（可用 DOMAIN_CONF=... 指定）"

# ---------------------------------------------------------------- 1. Python
say "1/7 检查 Python（需要 ≥3.9）"
PY=""
for cand in python3.12 python3.11 python3.10 python3.9; do
  if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
  # Alinux3 仓库里 python3.11 可用；3.9 不一定有，所以先试 3.11
  if dnf list --available python3.11 >/dev/null 2>&1; then
    say "未找到 3.9+，安装 python3.11（Alinux3 官方源）"
    dnf install -y python3.11 python3.11-pip >/dev/null || die "python3.11 安装失败"
    PY=python3.11
  else
    say "未找到 3.9+，尝试安装 python3.9"
    dnf install -y python3.9 >/dev/null || die "python3.9 安装失败，请手动安装后重跑"
    PY=python3.9
  fi
fi
"$PY" -c 'import sys; assert sys.version_info >= (3,9), sys.version' \
  || die "$PY 版本过低"
echo "使用解释器：$PY ($("$PY" -V 2>&1))"

# ---------------------------------------------------------------- 2. 代码
say "2/7 准备代码到 $APP_DIR"
TARBALL="${SRC_TARBALL:-/tmp/xinzhen-src.tar.gz}"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only || echo "⚠️ git pull 失败，继续用现有代码"
elif [ -f "$TARBALL" ]; then
  echo "使用离线包：$TARBALL"
  mkdir -p "$APP_DIR"
  tar -xzf "$TARBALL" -C "$APP_DIR" --strip-components=1 || die "解压失败"
else
  mkdir -p "$APP_DIR"
  echo "未发现离线包，尝试 git clone（国内服务器可能较慢/失败）"
  git clone --depth 1 "$REPO_URL" "$APP_DIR" || die "git clone 失败：请改用离线包，见部署说明 §10.2"
fi
[ -f "$APP_DIR/apps/server/requirements.txt" ] || die "代码结构异常（缺 requirements.txt）"

# ---------------------------------------------------------------- 3. venv
say "3/7 创建虚拟环境并安装依赖（只有 fastapi / uvicorn / pydantic）"
[ -d "$APP_DIR/venv" ] || "$PY" -m venv "$APP_DIR/venv" || die "venv 创建失败：确认已装 $PY-pip（dnf install -y ${PY}-pip）"
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/apps/server/requirements.txt" || die "依赖安装失败"

# ---------------------------------------------------------------- 4. .env
say "4/7 准备 .env（已存在则跳过）"
if [ ! -f "$APP_DIR/apps/server/.env" ]; then
  cp "$APP_DIR/deploy/env.production.example" "$APP_DIR/apps/server/.env"
  echo "已从模板生成 apps/server/.env"
else
  echo "apps/server/.env 已存在，保持不动"
fi
chmod 600 "$APP_DIR/apps/server/.env"

# ---------------------------------------------------------------- 5. 用户与目录
say "5/7 准备运行用户与目录"
id "$SERVICE_USER" >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin "$SERVICE_USER"
mkdir -p "$APP_DIR/www/xz" "$APP_DIR/apps/data"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR/apps/data"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR/www"
chmod 600 "$APP_DIR/apps/server/.env" && chown "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR/apps/server/.env"

# ---------------------------------------------------------------- 6. systemd
say "6/7 安装并启动 systemd 服务"
install -m 644 "$APP_DIR/deploy/xinzhen.service" /etc/systemd/system/xinzhen.service
systemctl daemon-reload
systemctl enable xinzhen >/dev/null 2>&1 || true
systemctl restart xinzhen
sleep 2
systemctl is-active --quiet xinzhen || { journalctl -u xinzhen -n 30 --no-pager; die "服务未起来，见上面日志"; }
curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null || die "健康检查失败（端口 ${PORT}）"
echo "后端已启动：http://127.0.0.1:${PORT}/api/health"

# ---------------------------------------------------------------- 7. Nginx
say "7/7 把 /xz/ 配置插入 $DOMAIN_CONF"
if grep -q 'location /xz/' "$DOMAIN_CONF"; then
  echo "已存在 /xz/ 配置，跳过"
else
  BACKUP="${DOMAIN_CONF}.bak.$(date +%Y%m%d%H%M%S)"
  cp "$DOMAIN_CONF" "$BACKUP"
  echo "已备份：$BACKUP"

  "$APP_DIR/venv/bin/python" - "$DOMAIN_CONF" "$APP_DIR/deploy/nginx-xz-snippet.conf" <<'PY'
import re
import sys

conf_path, snippet_path = sys.argv[1], sys.argv[2]
text = open(conf_path, encoding="utf-8").read()
snippet = open(snippet_path, encoding="utf-8").read().rstrip("\n")

if "location /xz/" in text:
    print("snippet already present")
    raise SystemExit(0)

# 找出所有 server { ... } 块（本项目配置简单，用花括号配对即可）
blocks = []
for m in re.finditer(r"(^|\n)([ \t]*server\s*\{)", text):
    start = m.end(2)
    depth, i = 1, start
    while i < len(text) and depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    head = text[max(0, m.start(2) - 200):start]
    blocks.append((m.start(2), start, i, head))

def insert_before_location_root(start, end):
    """在块内 'location / {' 之前插入；没有则插到块末尾。"""
    seg = text[start:end - 1]
    m = re.search(r"^[ \t]*location\s+/\s*\{", seg, re.M)
    pos = start + (m.start() if m else len(seg))
    return pos

targets = []
for (bstart, bstart_body, bend, head) in blocks:
    body = text[bstart_body:bend]
    if re.search(r"listen\s+443", body) and "location /xz/" not in body:
        targets.append((bstart_body, bend))
if not targets:
    for (bstart, bstart_body, bend, head) in blocks:
        body = text[bstart_body:bend]
        if re.search(r"listen\s+80", body) and "return 301" not in body:
            targets.append((bstart_body, bend))

if not targets:
    print("no suitable server block found")
    raise SystemExit(2)

# 从后往前插入，避免偏移失效
for (s, e) in sorted(targets, reverse=True):
    pos = insert_before_location_root(s, e)
    text = text[:pos] + snippet + "\n\n" + text[pos:]

open(conf_path, "w", encoding="utf-8").write(text)
print(f"inserted into {len(targets)} server block(s)")
PY

  if nginx -t 2>/tmp/xz-nginx-test.log; then
    systemctl reload nginx
    echo "Nginx 已重载"
  else
    echo "nginx -t 失败，回滚配置："
    cat /tmp/xz-nginx-test.log
    cp "$BACKUP" "$DOMAIN_CONF"
    die "已恢复原配置，未重载。请把上面报错发我"
  fi
fi
rm -f /tmp/xz-nginx-test.log

# ---------------------------------------------------------------- 收尾
cat <<EOF

============================================================
安装完成。还差两步（见 deploy/部署说明.md §6、§7）：

1) 上传前端（在你本机执行，dist 已按 VITE_BASE=/xz/ 构建）：
     scp -r apps/web/dist/* root@<你的服务器>:/opt/xinzhen/www/xz/

2) 打开 https://zerovoidlog.cn/xz/ 自检：
     - 顶栏应显示「后端已连接」
     - 点「C1 故障版」，中栏步骤应逐条流式出现（不是一次性刷出）

HTTPS 提醒：
$(grep -q 'listen[[:space:]]*443' "$DOMAIN_CONF" && echo '  ✓ 已检测到 443 配置' || echo '  ✗ 未检测到 443 —— 云端密钥会走明文 HTTP，务必先上证书：dnf install -y certbot python3-certbot-nginx && certbot --nginx -d zerovoidlog.cn -d www.zerovoidlog.cn')
============================================================
EOF
