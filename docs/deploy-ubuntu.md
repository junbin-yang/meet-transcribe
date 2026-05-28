# Ubuntu 服务器部署指南

适用：Ubuntu 22.04，NVIDIA GPU (CUDA 12.x)，PostgreSQL 16 + pgvector。

## 1. 环境准备

```bash
apt update && apt install -y \
  python3.11 python3.11-venv python3.11-dev \
  postgresql-16 postgresql-16-pgvector \
  build-essential libssl-dev libffi-dev \
  sox libsox-fmt-all ffmpeg
```

## 2. 数据库初始化

```bash
sudo -u postgres psql <<'SQL'
CREATE DATABASE meet_transcribe;
CREATE USER meet_transcribe WITH PASSWORD '<your_password>';
GRANT ALL PRIVILEGES ON DATABASE meet_transcribe TO meet_transcribe;
\c meet_transcribe
CREATE EXTENSION IF NOT EXISTS vector;
GRANT ALL ON SCHEMA public TO meet_transcribe;
SQL

psql -U meet_transcribe -d meet_transcribe -f deploy/scripts/init_schema.sql
```

## 3. 应用部署

```bash
bash deploy/scripts/install.sh

cp configs/meet-transcribe.example.yaml /etc/meet-transcribe/meet-transcribe.yaml

cat > /etc/meet-transcribe/env <<'EOF'
MT_DB_PASSWORD=<your_password>
MT_SERVER_SECRET=<random_32_chars>
MT_KMS_KEY=<base64_32_bytes>
MT_ADMIN_TOKEN=<random_32_chars>
PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
EOF
chmod 600 /etc/meet-transcribe/env
```

编辑 `/etc/meet-transcribe/meet-transcribe.yaml`：
- `database.url`: 数据库连接串
- `asr.model`: `large`（默认）
- `asr.device`: `cuda`

## 4. 模型预热

`install.sh` 最后一步自动运行 `scripts/pre_download_models.py` 预下载 4 个模型到
`~/.cache/modelscope/hub/models/`（约 1.5 GB，5-15 分钟）。

也可手动运行：

```bash
python scripts/pre_download_models.py
```

离线部署：在有网络的机器上运行一次，将 `~/.cache/modelscope/` 打包传输到目标机器。

## 5. 启动

```bash
systemctl enable --now meet-transcribe
curl http://localhost:8080/ready
```

## 6. 创建租户

### 6.1 一键创建

```bash
bash scripts/bootstrap-tenant.sh
```

脚本会依次执行：

1. `POST /v1/admin/tenants` — 创建 tenant（name、并发配额、日分钟配额）
2. `POST /v1/admin/tenants/{id}/api-keys` — 为该 tenant 签发 API Key
3. `POST /v1/auth/ticket` — 用 API Key 换一个短期 ticket（验证用）

### 6.2 输出文件

| 文件 | 内容 | 用途 |
|------|------|------|
| `.scratch/tenant.json` | tenant 完整信息（id, name, quota） | 运维留存 |
| `.scratch/apikey.json` | API Key 签发响应 | 交付给客户 |
| `.scratch/api_key.txt` | API Key 明文（`mt_...` 格式） | **交付给客户** |
| `.scratch/ticket.json` | ticket 签发响应 | 运维验证 |
| `.scratch/ticket.txt` | ticket 明文（30s 有效） | 粘贴到 web demo 测试 |
| `.scratch/ws_url.txt` | 含 ticket 的完整 WS URL | 粘贴到 web demo 测试 |

### 6.3 交付给第三方客户

客户需要的**唯一凭证是 API Key**（`api_key.txt` 中的 `mt_...` 字符串）。

- 无需 tenant ID、无需 UUID、无需了解数据库结构
- 客户在 HTTP 请求中携带 `Authorization: Bearer <api_key>`
- 服务端通过 HMAC-SHA256 将 API Key 映射到 tenant，客户无感知
- 一个 tenant 可以有多个 API Key（不同客户端、不同环境）

### 6.4 手动管理

```bash
# 创建 tenant
curl -X POST http://localhost:8080/v1/admin/tenants \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: $MT_ADMIN_TOKEN" \
  -d '{"name":"client-a","quota_concurrent":5,"quota_minutes_per_day":600}'

# 签发 API Key
curl -X POST http://localhost:8080/v1/admin/tenants/{tenant_id}/api-keys?label=prod \
  -H "X-Admin-Token: $MT_ADMIN_TOKEN"
# 返回 {"id":"...","api_key":"mt_...","label":"prod"}
# api_key 仅在创建时返回一次，不可再次获取
```

### 6.5 API Key 安全说明

- `api_key` 明文**仅在签发时返回一次**，之后无法从服务端查询
- 数据库只存储 `key_hash = HMAC-SHA256(server_secret, api_key)`，无法反推
- 客户遗失 API Key → 重新签发新的，吊销旧的
- API Key 通过 HTTP `Authorization` 头传输，必须使用 HTTPS

## 7. 运维

```bash
journalctl -u meet-transcribe -f   # 日志
systemctl restart meet-transcribe  # 重启
nvidia-smi                         # GPU 显存
curl http://localhost:8080/metrics # 指标
ufw allow 8080/tcp                 # 防火墙
```

## 8. 资源要求

| 资源 | 最低 | 推荐 |
|------|------|------|
| GPU 显存 | 4 GB | 8 GB |
| 内存 | 8 GB | 16 GB |
| 磁盘 | 10 GB | 50 GB (含模型) |
| CPU | 4 核 | 8 核 |
