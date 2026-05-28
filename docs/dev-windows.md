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
# 1. 确保 PostgreSQL 运行中（通常开机自启）
pg_isready -U postgres

# 2. 激活虚拟环境
source .venv/Scripts/activate

# 3. 启动服务
python scripts/run_dev.py
```

验证：`curl http://127.0.0.1:18080/ready` → `{"status":"ok"}`

首次启动下载模型约 15-30 分钟，后续启动约 30 秒。

### 4.1 日志

日志默认输出到终端（JSON 格式）。建议重定向到文件：

```bash
python scripts/run_dev.py > logs/server.log 2>&1 &
tail -f logs/server.log
```

关键日志事件：

| 事件 | 含义 |
|------|------|
| `funasr.spk_engine.init` | 模型加载完成 |
| `Application startup complete` | 服务就绪 |
| `model.run.done` | SPK 模型运行完成 |

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
