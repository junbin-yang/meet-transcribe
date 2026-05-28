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

## 4. 模型预热（首次启动）

首次启动自动从 ModelScope 下载模型到 `~/.cache/modelscope/hub/models/`，约 5-10 分钟。

离线部署：在有网络的机器上运行一次 `python scripts/run_dev.py`，然后将 `~/.cache/modelscope/` 打包传输到目标机器。

## 5. 启动

```bash
systemctl enable --now meet-transcribe
curl http://localhost:8080/ready
```

## 6. 创建租户

```bash
bash scripts/bootstrap-tenant.sh
```

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
