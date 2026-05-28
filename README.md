# meet-transcribe

会议场景实时语音转写后端服务（B2B 私有化部署）。

基于 [WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit) 二次开发，新增多租户、说话人注册、热词、可观测性、合规与运维能力。

## 状态

MVP 阶段。详见 v2 设计文档 `docs/design-v2.md`。

## 快速开始（开发者）

```bash
# 1. 准备 Python 3.11 venv
py -3.11 -m venv .venv
source .venv/Scripts/activate

# 2. 拉取 vendored WhisperLiveKit + 安装依赖（在线）
git submodule update --init --recursive
pip install -e ".[dev]"

# 3. 起本地 PostgreSQL + pgvector，落 schema
psql -U postgres -f deploy/scripts/init_schema.sql

# 4. 复制配置模板，并准备 .env（不入库）
cp configs/meet-transcribe.example.yaml configs/meet-transcribe.yaml
cat > .env <<'EOF'
MT_DB_PASSWORD=<32+ urlsafe>
MT_SERVER_SECRET=<32+ urlsafe>
MT_KMS_KEY=<base64 32B>
MT_ADMIN_TOKEN=<32+ urlsafe>
PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
EOF
chmod 600 .env

# 5. 启动开发服务（Windows 用 run_cli，自动设置 selector loop）
.venv/Scripts/python.exe -m uvicorn meet_transcribe.api.app:app \
  --host 127.0.0.1 --port 18080 --loop asyncio

# 6. 一键开通 demo 租户、签 API Key、换 ticket
bash scripts/bootstrap-tenant.sh   # 输出写入 .scratch/

# 7. 打开 Web Demo（http://localhost:18080/demo），粘贴 .scratch/ticket.txt
```

> 首次连接 WebSocket 会触发 faster-whisper 下载 medium FP16 权重（约 1.5 GB），
> 进度可在 uvicorn 日志中看到。下载结束后才会出现首条转写结果。

## 目录结构

```
src/meet_transcribe/
  api/             FastAPI 路由 + WebSocket 入口
  auth/            API Key + ticket 鉴权
  core/            Session Orchestrator + Inference Worker
  db/              SQLAlchemy 模型 + 迁移
  diarization/     说话人分离上下文 + 适配 Sortformer/Diart
  speakers/        声纹注册 + ECAPA-TDNN + pgvector 匹配
  hotwords/        热词管理 + initial_prompt 注入
  sessions/        会话生命周期与持久化
  observability/   structlog + Prometheus
  config/          YAML + env 配置加载
vendored/
  whisperlivekit/  fork 的 WhisperLiveKit（pinned commit）
deploy/
  systemd/         meet-transcribe.service 模板
  scripts/         install.sh / doctor.sh / init_schema.sql
  wheelhouse/      离线 wheel 仓库（M4 后填充）
web-demo/          最简 HTML + MediaRecorder
configs/           YAML 模板
tests/             unit / integration / e2e
docs/              设计文档 + 部署文档
```

## License

待定（内部 B2B 项目，不开源）。

## 文档

- `docs/design-v2.md` 设计文档（来自 office-hours + adversarial review 合成）
- `docs/deploy.md` 部署文档（待补，M4）
- `docs/protocol.md` WebSocket 协议规范（待补，M1）
