# Windows 开发环境指南

## 1. 环境要求

- Python 3.11
- PostgreSQL 16 + pgvector 扩展
- NVIDIA GPU + CUDA 12.x（可选，CPU 可跑但较慢）

## 2. 快速开始

```bash
py -3.11 -m venv .venv
source .venv/Scripts/activate
pip install -e ".[dev]"
psql -U postgres -f deploy/scripts/init_schema.sql
cp configs/meet-transcribe.example.yaml configs/meet-transcribe.yaml
```

## 3. 环境变量

创建 `.env`：

```
MT_DB_PASSWORD=<your_password>
MT_SERVER_SECRET=<32+ chars>
MT_KMS_KEY=<base64 32 bytes>
MT_ADMIN_TOKEN=<32+ chars>
```

## 4. 启动

```bash
python scripts/run_dev.py
# http://127.0.0.1:18080/ready → {"status":"ok"}
```

首次启动自动下载 FunASR 模型到 `~/.cache/modelscope/`（约 15-30 分钟）。

## 5. Web Demo

`http://127.0.0.1:18080/demo`

## 6. 创建测试租户

```bash
bash scripts/bootstrap-tenant.sh
```

## 7. 测试

```bash
python -m pytest tests/ -v
python -m pytest tests/ --cov=src --cov-report=term-missing
```

## 8. 模型缓存

| 模型 | 大小 |
|------|------|
| `paraformer-zh` (ASR) | ~944 MB |
| `fsmn-vad` (VAD) | ~5 MB |
| `ct-punc` (标点) | ~500 MB |
| `cam++` (说话人) | ~27 MB |
