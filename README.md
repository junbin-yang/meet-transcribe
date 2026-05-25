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

# 2. 安装依赖（在线）
pip install -e ".[dev]"

# 3. 起本地 PostgreSQL + pgvector，落 schema
psql -U postgres -f deploy/scripts/init_schema.sql

# 4. 复制配置模板
cp configs/meet-transcribe.example.yaml configs/meet-transcribe.yaml

# 5. 启动开发服务
uvicorn meet_transcribe.api.app:app --reload --port 8080

# 6. 打开 Web Demo
# 浏览器访问 http://localhost:8080/demo
```

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
