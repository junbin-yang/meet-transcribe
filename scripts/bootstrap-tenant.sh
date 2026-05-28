#!/usr/bin/env bash
# 在已运行的 meet-transcribe 上一键创建 tenant + api_key + ticket。
#
# 前置条件：
#   1. uvicorn 已起在 $HOST:$PORT
#   2. .env 已含 MT_ADMIN_TOKEN / MT_SERVER_SECRET / MT_DB_PASSWORD / MT_KMS_KEY
#   3. PG 已 schema 初始化（deploy/scripts/init_schema.sql）
#
# 输出：
#   .scratch/api_key.txt   一次性返回的明文 API Key（妥善保管）
#   .scratch/ticket.txt    短期一次性 ticket（默认 30s 有效）
#   .scratch/ws_url.txt    含 ticket 参数的 ws:// URL，可直接粘到 web-demo

set -euo pipefail

HOST="${MT_HOST:-127.0.0.1}"
PORT="${MT_PORT:-18080}"
TENANT_NAME="${MT_TENANT_NAME:-acme-demo-$(date +%s)}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

mkdir -p .scratch

PYTHON=".venv/Scripts/python.exe"
[ -x "$PYTHON" ] || PYTHON=".venv/bin/python"

ADMIN=$("$PYTHON" -c "from meet_transcribe.config.loader import load_config; print(load_config().secrets.admin_token.get_secret_value())")
if [ -z "$ADMIN" ]; then
  echo "ERROR: MT_ADMIN_TOKEN 未配置（.env 缺失）" >&2
  exit 2
fi

BASE="http://$HOST:$PORT"

echo ">> 创建 tenant: $TENANT_NAME"
curl -fsS -X POST "$BASE/v1/admin/tenants" \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: $ADMIN" \
  -d "{\"name\":\"$TENANT_NAME\",\"quota_concurrent\":1,\"quota_minutes_per_day\":60}" \
  > .scratch/tenant.json

TID=$("$PYTHON" -c "import json; print(json.load(open('.scratch/tenant.json'))['id'])")
echo "   tenant_id=$TID"

echo ">> 签发 API Key"
curl -fsS -X POST "$BASE/v1/admin/tenants/$TID/api-keys?label=demo" \
  -H "X-Admin-Token: $ADMIN" \
  > .scratch/apikey.json

API_KEY=$("$PYTHON" -c "import json; print(json.load(open('.scratch/apikey.json'))['api_key'])")
echo "$API_KEY" > .scratch/api_key.txt
echo "   api_key 已写入 .scratch/api_key.txt"

echo ">> 换 ticket"
curl -fsS -X POST "$BASE/v1/auth/ticket" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"session_hint":"demo"}' \
  > .scratch/ticket.json

TTL=$("$PYTHON" -c "import json; print(json.load(open('.scratch/ticket.json'))['expires_in'])")
TICKET=$("$PYTHON" -c "import json; print(json.load(open('.scratch/ticket.json'))['ticket'])")
echo "$TICKET" > .scratch/ticket.txt

WS_URL="ws://$HOST:$PORT/v1/ws/transcribe?ticket=$TICKET"
echo "$WS_URL" > .scratch/ws_url.txt

cat <<EOF

完成。

  Tenant ID : $TID
  API Key   : (.scratch/api_key.txt, 长度 ${#API_KEY})
  Ticket    : (.scratch/ticket.txt, ${TTL}s 有效, 一次性)
  WS URL    : $WS_URL

下一步：打开 http://$HOST:$PORT/demo/，把 Ticket 粘到输入框；
ticket 过期需重新跑本脚本。
EOF
