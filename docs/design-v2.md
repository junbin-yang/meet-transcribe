# meet-transcribe 设计文档 v2

**Mode:** Builder（B2B 商业内创，非经典初创）
**Generated:** 2026-05-25 11:26:42
**Supersedes:** `28276-unknown-design-20260525-110147.md`（v1，已被对抗式评审推翻多处假设）
**User/Branch:** 28276 / unknown
**Skill:** /office-hours → /autoplan adversarial review
**Status:** v2 草稿（吸收 4 个视角共 35 条 findings 的合成结果）

> **v1 → v2 主要变化**：
> - 跨租户状态污染从"风险表低概率项"升级为"架构主线第 4 节"
> - 放弃"import WhisperLiveKit 库"假设，改为 **fork 内嵌 + 适配层**
> - 新增第 12（性能与质量数字）、13（协议细节与错误码）、14（合规与数据生命周期）节
> - 鉴权方案改为 HTTP ticket → WebSocket
> - 声纹与热词承诺降级，验收口径量化
> - 里程碑加 M4.5（诊断脚本）和 M5（首家客户交付，1.5–2 周）

---

## 0. 一句话定位

一个**私有化部署的、面向会议场景的实时语音转写后端服务**：在 fork 后的 WhisperLiveKit 之上做工程化封装，增加"说话人注册识别 + 热词 + 多租户 + 可运维 + 合规"五件套，作为可嵌入第三方会议系统的 B2B 服务交付。

不是再造一个 Whisper 服务，是把研究级原型变成可向客户交付的私有化产品。

---

## 1. 选择路径（Path Selection）

| 维度 | 选择 |
|---|---|
| 工作模式 | Builder（已有明确产品形态与客户需求） |
| 核心能力 | 转写 + 说话人分离（diarization） |
| 消费方 | 外部客户 / 第三方系统（B2B，私有化部署到客户机房） |
| MVP 范围 | 单机 1–3 路并发、纯实时 |
| 二开增量 | 工程化（多租户/可观测/鉴权/合规）+ 说话人注册 + 热词（降级 P1） |
| 实现路径 | **方案 B'：fork + 适配层**（不再 import 第三方库，而是 fork 到 `vendored/whisperlivekit/`，通过 `whisperlivekit_adapter.py` 隔离） |
| 交付形态 | 私有化部署 + 原生进程（systemd + Python venv，不用容器） |

---

## 2. 上下文（What the user is actually building）

### 2.1 真实意图

- **场景**：会议系统集成（会议室 / 视频会议 / 客户的某个上层产品）
- **角色**：作为后端转写引擎被上层会议系统调用
- **物理形态**：私有化部署到客户机房，不能依赖容器
- **GPU**：用户本机 ≥ 12GB VRAM；**但客户机房 GPU 不等于开发机 GPU**（见 P2 修订）
- **前端**：最简 Web Demo
- **当前仓库**：只做服务端

### 2.2 项目本质

仍然是"WhisperLiveKit 的 B2B 商业化壳层"，但 v2 明确：
- WhisperLiveKit 解决了算法层 80% 硬骨头
- 但其代码**不稳定**（无 SemVer、研究项目），所以走 fork + 适配层路径
- v2 新增"合规壳层"（PIPL / 数据安全法 / 等保 2.0 三级）作为交付必需

### 2.3 不做什么

- 不做翻译
- 不做摘要 / LLM 后处理
- 不做录播离线批转写
- 不做横向集群 / HA
- 不重写 WhisperLiveKit 的核心算法
- **新增不做**：不做完整 PIPL 套件（同意/通知/数据出境评估），只承诺"我们提供 hash + 删除能力，上层会议系统负责取得用户同意"

---

## 3. 跨模型视角 / 现状梳理（WhisperLiveKit 能力地图）

### 3.1 WhisperLiveKit 已经提供

| 能力 | 状态 | 备注 |
|---|---|---|
| FastAPI + WebSocket 服务骨架 | 完整但**不直接用** | v2 fork 后仅复用算法类，不沿用其 server.py |
| faster-whisper 后端 | 完整 | 默认推理后端，CTranslate2 加速 |
| WhisperStreaming（LocalAgreement） | 完整 | 开源策略，可商用 |
| SimulStreaming（AlignAtt） | 双重许可 | **MIT 仅非商用**，v2 启动时**硬关并 CI 守门** |
| Streaming Sortformer | 完整 | 优先选项 |
| Diart | 完整 | 显存吃紧时 fallback |
| VAD（Silero） | 完整 | 静音段不喂模型 |

### 3.2 WhisperLiveKit 缺什么（v2 仍补，但优先级有调整）

| 缺口 | 原 v1 优先级 | v2 优先级 | 调整理由 |
|---|---|---|---|
| 鉴权（API Key + HTTP ticket → WS） | P0 | P0 | 方案改为 ticket（见第 4.3） |
| 多租户隔离 | P0 | P0 | 升级到架构主线，每 session 独立 AudioProcessor + diarization context |
| 说话人注册库 | P0 | P0 | 降级承诺面（仅注册者识别） |
| 热词管理 | P0 | **P1** | over-promise，降级 |
| 结构化日志 + Prometheus 指标 | P0 | P0 | 加具体指标 schema |
| 配置中心化 | P1 | **P0** | 部署可复制性的前提 |
| 会话生命周期管理 | P1 | P1 | 不变 |
| 转写持久化 | P1 | P1 | 加加密 + 保留期 |
| systemd 部署脚本 | P0 | P0 | 加 supervisor fallback |
| **诊断脚本（doctor）** | 无 | P0 | 新增，B2B #7 |
| **合规字段 + 数据生命周期** | 无 | P0 | 新增，PIPL/数据安全法 |
| **GPU 售前调查清单** | 无 | P0 | 新增，B2B #8 |

### 3.3 关键技术选型

| 项 | 选择 | 理由 |
|---|---|---|
| 模型 | large-v3-turbo（≥12GB GPU）/ medium-fp16（8GB）/ small-int8（6GB） | 加降级矩阵 |
| 推理后端 | faster-whisper | CTranslate2 加速 |
| 流式策略 | WhisperStreaming（LocalAgreement） | 开源可商用 |
| 说话人分离 | Streaming Sortformer 优先 / Diart fallback | 不变 |
| 声纹特征 | **SpeechBrain ECAPA-TDNN（192 维）** | 轻、快、Python 集成简单 |
| 向量库 | **PostgreSQL + pgvector + HNSW(m=16, ef_construction=64)** + 应用层 LRU 缓存；**SQLite 余弦相似度** fallback | 客户无 pgvector 时降级 |
| Web 框架 | FastAPI + uvicorn | 不变 |
| GPU 调度 | **asyncio 主循环 + 单一推理线程池（max_workers=1）** | 见第 4.5 并发模型 |
| 进程管理 | systemd + venv，**专用用户 `meet-transcribe`** + supervisor 用户态 fallback | 客户安全策略适配 |
| 日志 | structlog + JSON | 接客户日志采集 |
| 指标 | prometheus-client | 加具体指标 schema（见第 12.4） |
| 配置 | **YAML + 12-factor env 覆盖** | 部署可复制（新增 P0） |

---

## 4. 架构（方案 B'：fork + 适配层 + 严格每会话隔离）

### 4.1 进程内拓扑

```
+----------------------------------------------------------------+
|   meet-transcribe 单进程 (uvicorn workers=1, asyncio main)      |
|                                                                |
|  +---------------------------------------------+               |
|  | FastAPI App                                 |               |
|  | - /health, /ready, /metrics                 |               |
|  | - /v1/auth/ticket  HTTP -> 短期 ticket      |               |
|  | - /v1/tenants/*  /v1/speakers/*  /v1/hotwords/*  /v1/sessions/* |
|  | - /v1/ws/transcribe?ticket=...  WebSocket   |               |
|  +---------------------------------------------+               |
|         |                                                      |
|         v                                                      |
|  +---------------------------------------------+               |
|  | Session Orchestrator                        |               |
|  | - 校验 ticket (一次性, TTL 30s)             |               |
|  | - 定位 tenant; 检查配额                     |               |
|  | - **每个 session 实例化独立的**:            |               |
|  |   * AudioProcessor (WhisperLiveKit fork)    |               |
|  |   * DiarizationContext (speaker_id 计数器)  |               |
|  |   * HotwordContext (initial_prompt)         |               |
|  |   * SeqGenerator (partial seq + stable_until)|              |
|  +---------------------------------------------+               |
|         |                                                      |
|         v                                                      |
|  +---------------------------------------------+               |
|  | Inference Worker (单线程池)                  |               |
|  | - 串行执行 GPU 推理                          |               |
|  | - 单一 TranscriptionEngine 单例（无状态）    |               |
|  | - PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True |        |
|  +---------------------------------------------+               |
|         |              |              |                        |
|         v              v              v                        |
|  +----------+ +--------+ +--------+ +-------+                  |
|  |Speaker   | |Hotword | |Audit  | |Doctor |                   |
|  |Matcher   | |Manager | |Logger | |Probes |                   |
|  |HNSW LRU  | |按租户  | |等保  | |GPU健康|                    |
|  +----------+ +--------+ +--------+ +-------+                  |
+----------------------------------------------------------------+
         |              |              |
         v              v              v
  +-------------------------------------------------+
  | PostgreSQL + pgvector (或 SQLite fallback)       |
  | - tenants / api_keys / consents                 |
  | - speakers (embedding vector(192) HNSW)         |
  | - hotwords / sessions / transcripts (AES-GCM)   |
  | - audit_logs (等保 2.0 留存 ≥ 180 天)            |
  +-------------------------------------------------+
```

### 4.2 隔离原则（v2 主线，针对 C1 跨租户污染）

**每个 WebSocket session 拥有独立**：
- `AudioProcessor` 实例（含 LocalAgreement 的稳定/未稳定队列、滑窗缓冲）
- `DiarizationContext`（含 speaker_internal_id 计数器、Sortformer 状态）
- `HotwordContext`（initial_prompt 字符串）
- `SeqGenerator`（partial 的 seq + stable_until）

**全局共享只允许**：
- 单一 `TranscriptionEngine` 单例（模型权重，**无状态推理函数**）
- 数据库连接池

**端到端验收测试**：两个不同 tenant 的 session 并发，互讲对方业务词，验证**绝无串字**。失败即阻塞 release。

### 4.3 鉴权方案（v2 改为 ticket 模式）

旧（v1，已废弃）：`Sec-WebSocket-Protocol: bearer.<api_key>` → 反代日志泄露风险

新（v2）：
1. 客户端 HTTP POST `/v1/auth/ticket`，body `{"api_key":"...","intent":"transcribe"}`，TLS 加密
2. 服务端校验 api_key（HMAC-SHA256 比对 `api_keys.key_hash`），返回 `{"ticket":"<random128bit>","expires_in":30}`
3. 客户端建 WS：`wss://host/v1/ws/transcribe?ticket=<ticket>`
4. 服务端原子消费 ticket（Redis SETNX 或 PG advisory lock）、绑定 tenant_id，删除 ticket

**密钥派生**：`api_keys.key_hash = HMAC-SHA256(server_secret, api_key)`，server_secret 在配置文件中通过环境变量注入。

### 4.4 WebSocket 协议（对外）

**入站**：
- 第一帧（JSON 控制帧，**严格 schema**，size ≤ 4KB）：
  ```json
  {
    "type": "start",
    "session_id": "uuid",
    "language": "zh",
    "hotwords": ["..."],
    "speaker_set": "meeting-room-7",
    "audio_format": "pcm_s16le_16k"
  }
  ```
- 后续帧：二进制音频（PCM 16k mono int16；首帧规定的 format 之外的格式直接 AUDIO_FORMAT_INVALID）

**出站**（v2 增加 seq 与 stable_until）：
- partial：
  ```json
  {"type":"partial","seq":42,"stable_until":38,"text":"...","speaker":"S1","speaker_label":"张三?","confidence":0.82}
  ```
  - `seq` 单调递增；客户端按 seq 覆盖
  - `stable_until` 之前的 seq 已稳定，不再回撤
- final：
  ```json
  {"type":"final","seq":43,"start":1.23,"end":3.45,"text":"...","speaker":"S1","speaker_label":"张三","words":[...]}
  ```
- 时间基：`start`/`end` = 从 session 开始的相对秒数，`words[i].start/end` 同基
- ping：`{"type":"ping"}`（服务端每 15s 发一次，客户端 30s 无 pong 则断开）
- error：见第 13.2 错误码表

### 4.5 并发模型（v2 显式声明，针对 H2）

- uvicorn `workers=1`
- asyncio 主循环只做**音频帧搬运 + JSON 编解码**
- GPU 推理在 `concurrent.futures.ThreadPoolExecutor(max_workers=1)` 中**串行执行**
- 3 路并发的语义 = 3 个 AudioProcessor 实例**轮流**喂模型，**峰值首 partial 延迟随并发数线性增长**
- 实测上限：3 路并发时首 partial p95 ≤ 800ms × 3 ≈ 2.4s 是可能上限；如超 1.5s 用户体感不佳，则降到 2 路或换 medium 模型
- **明确不支持 GPU 上 batch 推理**（faster-whisper 不擅长动态 batch，留 v1.1 再做）

### 4.6 数据模型（v2 增补合规字段）

```sql
tenants(
  id, name, created_at,
  quota_concurrent, quota_minutes_per_day,
  data_retention_days  -- 新增：转写保留天数
);

api_keys(
  id, tenant_id, key_hash, label, created_at, revoked_at
  -- key_hash = HMAC-SHA256(server_secret, plaintext)
);

speakers(
  id, tenant_id, name, embedding vector(192),
  sample_count, snr_db_avg,           -- 新增：注册样本质量
  consent_at, consent_source,         -- 新增：同意时间与来源（PIPL）
  created_at, deleted_at              -- 软删除，到期物理删除
);

hotwords(
  id, tenant_id, scope, scope_id, word, weight, created_at
);

sessions(
  id, tenant_id, started_at, ended_at, status, speaker_set_ref
);

transcripts(
  id, session_id,
  start_sec, end_sec,
  speaker_internal_id, speaker_resolved_id,
  text_encrypted BYTEA,               -- 新增：AES-GCM 应用层加密
  text_iv BYTEA, text_tag BYTEA,
  is_final, created_at
);

audit_logs(
  id, ts, tenant_id, actor, action, resource_type, resource_id,
  detail_json, ip
  -- 等保 2.0 三级要求留存 ≥ 180 天
);
```

`speakers.embedding` 用 pgvector HNSW 索引（cosine）：
```sql
CREATE INDEX ON speakers USING hnsw (embedding vector_cosine_ops)
  WITH (m=16, ef_construction=64);
```

应用层 LRU 缓存最近 N（默认 256）个 tenant 的 top-100 speaker，避免热路径每次查 DB。

---

## 5. 前提（Premises）—— v2 修订

| # | v1 → v2 修订内容 |
|---|---|
| **P1** | 不变：客户接受私有化 + 我方提供 systemd unit 与文档 |
| **P2 (修订)** | 客户机房 GPU 不假设与开发机一致。**售前必做 GPU 调查清单**（型号 / 显存 / 驱动 / vGPU / 是否独占 / cuDNN 版本）。降级矩阵：≥12GB → turbo / 8GB → medium fp16 / 6GB → small int8 / 纯 CPU 拒接 |
| **P3** | 不变：MVP 单机 1–3 路并发，单模型 + 单推理线程 |
| **P4 (改写)** | **fork WhisperLiveKit 到 `vendored/whisperlivekit/`**，固定 sha；改动通过 `src/meet_transcribe/adapters/whisperlivekit_adapter.py` 隔离；upstream 合并走契约测试 + 人工 review。**不再假设上游 API 稳定** |
| **P5** | 不变：放弃 SimulStreaming；CI 加守门规则（grep 到 `SimulStreaming` import 即 fail） |
| **P6 (修订)** | 声纹注册：**样本时长 ≥ 15s 且 SNR ≥ 15dB 且话筒非远场**才接受；不满足时拒绝注册并返回原因。验收承诺改为：已注册说话人在 SNR ≥ 15dB 录音上匹配准确率 ≥ 80%；未注册者一律标 anonymous |
| **P7 (修订)** | systemd + venv 用专用用户 `meet-transcribe`（无 sudo），`CapabilityBoundingSet=` 收窄；不支持 systemd 的环境用 supervisor 用户态守护；提供 install.sh 与 uninstall.sh |
| **P8 (修订)** | PostgreSQL + pgvector 由我方提供安装指南；客户无 pgvector 权限或用国产 DB 时，**自动降级到 SQLite + 应用层余弦相似度**（适用于 < 1000 speaker/tenant） |
| **P9** | 不变：前端只做 Demo |
| **P10 (新增)** | 合规边界：我方承担"数据脱敏 + 加密存储 + 删除可执行 + 审计日志"；用户同意的取得 + 出境评估 + 第三方共享同意由上层会议系统承担。合同模板写清 |

---

## 6. 风险表（v2 升级）

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| **跨租户状态污染（C1）** | **高** | **高** | 每 session 独立 AudioProcessor/DiarizationContext/HotwordContext；端到端双租户串字测试列为 release 阻塞 |
| **WhisperLiveKit API 不稳定（C3）** | **高** | **高** | fork + 适配层；upstream 合并前跑契约测试集 |
| **声纹注册在真实会议室不可达（C2-1）** | **高** | **高** | 注册接口强制 SNR ≥ 15dB；准确率承诺改为"已注册者 ≥ 80%"；未注册标 anonymous |
| **合规缺失（C2-2）** | 中 | 高 | speakers.consent_at 字段；transcripts 加密 + 保留期；audit_logs 表；删除 API |
| **首家客户裸机部署失败（C4）** | 高 | 高 | M5 首家交付独立列出 1.5–2 周；install.sh 在 RHEL 8 + Kylin V10 双发行版冒烟；离线 wheelhouse 含 Miniforge |
| SimulStreaming 商用许可被误用 | 中 | 高 | 启动硬关 + CI 守门 + README 警示 |
| GPU 显存碎片化 OOM | 中 | 中 | PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True；systemd auto-restart；GPU 内存指标告警 |
| WebSocket 鉴权泄露 | 中 | 高 | ticket 模式；TLS 强制；ticket TTL 30s |
| 中文热词被分词错误反而拉低准确率 | 低 | 中 | initial_prompt 用空格分隔；提供 hotword 试听 API |
| 客户 PG 无 pgvector | 中 | 低 | SQLite + 余弦相似度 fallback |
| 客户网络无法 pip install | 中 | 中 | 离线 wheelhouse + Miniforge + install.sh --offline |
| 客户 GPU 不达标 | 中 | 高 | 售前 GPU 调查清单 + 降级矩阵 |

---

## 7. MVP 范围与里程碑（v2 增加 M4.5、M5）

### M0：本地能跑（Week 0，3 天）

- [ ] **fork** WhisperLiveKit 到 `vendored/whisperlivekit/`，固定 sha
- [ ] 写 pyproject.toml；whisperlivekit 作为本地路径依赖
- [ ] 写适配层 `adapters/whisperlivekit_adapter.py`（构造、调用、回调隔离）
- [ ] 跑通 large-v3-turbo + WhisperStreaming + Sortformer 本机示例
- [ ] **验收**：本地 Web Demo 录音能看到带 speaker 标签的转写流

### M1：API 与鉴权骨架（Week 1）

- [ ] 自建 FastAPI；ticket 模式鉴权
- [ ] PostgreSQL schema（tenants / api_keys / audit_logs）上线；pgvector 扩展安装脚本
- [ ] structlog JSON 日志 + Prometheus 指标骨架（含第 12.4 节指标）
- [ ] `/health`、`/ready`、`/metrics` 端点（/ready 必须验证 模型加载 + DB 连通 + GPU 可分配）
- [ ] YAML 配置 + 12-factor env 覆盖
- [ ] 端到端**双租户串字测试**列入 CI
- [ ] **验收**：错 API Key 被 401；正确的可建 WS；双租户并发无串字

### M2a：声纹注册离线（Week 2 上半）

- [ ] `/v1/speakers` CRUD（POST 接受音频样本）
- [ ] SpeechBrain ECAPA-TDNN 特征提取
- [ ] SNR 检测；样本质量不达标拒绝
- [ ] pgvector HNSW 存储 + 余弦相似度匹配；LRU 缓存
- [ ] consent 字段写入
- [ ] **验收**：用 AISHELL-4 抽样集，注册集 ≥ 80% top-1 准确率

### M2b：实时流匹配（Week 2 下半）

- [ ] WebSocket 流式过程中按 diarization 的 speaker_internal_id 实时查 matcher
- [ ] partial/final 输出 speaker_label 与 confidence
- [ ] **验收**：注册 3 人开会，已注册者识别率 ≥ 80%，未注册者一律 anonymous

### M3：会话管理 + 热词（P1 降级版）（Week 3）

- [ ] `/v1/sessions/*` 状态机
- [ ] 转写持久化（AES-GCM 加密，保留期可配）
- [ ] `/v1/hotwords` 租户级与会话级
- [ ] start 控制帧 hotwords 数组注入 initial_prompt
- [ ] hotword 试听 API（/v1/hotwords/preview 返回该 prompt 下的样例转写）
- [ ] **验收**：20+ 业务词的客户实测，**统计**准确率提升（不承诺单词级保证）

### M4：可部署化骨架（Week 3 末，2 天）

- [ ] systemd unit 模板（专用 user + Capability 收窄）+ supervisor fallback
- [ ] install.sh / uninstall.sh
- [ ] 离线 wheelhouse + Miniforge 打包
- [ ] 部署 README（GPU 驱动 / CUDA / cuDNN / glibc 矩阵 / PG + pgvector / SELinux）
- [ ] **验收**：干净 Ubuntu 22.04 上 2 小时内起服务

### M4.5：诊断与运维（Week 3 末加 2 天）

- [ ] `meet-transcribe-doctor` 脚本（GPU 检测 / DB 连通 / 模型加载 / 端口监听 / 日志样例）
- [ ] GPU 内存水位告警（Prometheus alert rule 模板）
- [ ] 钉钉 / 企微 webhook 通知模板（错误率、GPU OOM）
- [ ] 故障排查 FAQ（≥ 15 条常见错误）
- [ ] **验收**：客户运维半夜来电时，doctor 输出能直接定位问题

### M5：首家客户交付（Week 4–5，1.5–2 周）

- [ ] 售前 GPU 调查清单走完
- [ ] 在 RHEL 8（glibc 2.28）+ Kylin V10 双发行版冒烟通过
- [ ] 离线 wheelhouse 在客户机房（无互联网）跑通
- [ ] 客户运维独立按 README 完成一次安装、一次升级、一次回滚
- [ ] 7 天 GPU 内存监控数据回收，无 OOM
- [ ] **验收**：首家客户验收会议跑通；签字

总周期：**3 周开发 + 0.5 周打磨 + 1.5–2 周首家交付 ≈ 5–5.5 周**（v1 严重低估为 3.5 周）

---

## 8. 不在 MVP 范围

- 多机集群 / 负载均衡 / HA
- 离线批转写
- 翻译 / 摘要 / 会议纪要
- 管理后台 UI
- 多语言自动切换
- 端到端国密改造（但 AES-GCM 应用层加密支持替换为国密 SM4-GCM）
- WebRTC 协议
- **GPU 动态 batch 推理**（v1.1）
- **中文 ITN / 数字归一化**（MVP 关闭，标点由 Whisper 原生输出；v1.1 加 wetext 或自研）

---

## 9. 与 WhisperLiveKit 的关系契约（v2 重写）

- **形态**：fork 到本仓库 `vendored/whisperlivekit/`，作为子模块或子目录提交
- **入口隔离**：所有调用走 `src/meet_transcribe/adapters/whisperlivekit_adapter.py`
- **不沿用**：其 server.py / __main__.py / WebSocket 处理
- **沿用算法类**：`TranscriptionEngine` / `AudioProcessor` / Sortformer wrapper / Silero VAD wrapper
- **升级流程**：fetch upstream → 跑契约测试 → 人工 review diff → merge → 跑双租户串字测试

契约测试集（M0 末就要有）：
- AISHELL 抽样 100 条录音；CER 漂移阈值 ±2%
- 双租户并发串字检测
- GPU 内存峰值 ±10%

---

## 10. 下一步动作（v2 修订）

### 立即做（接下来 7 天）

1. **验证 C3 + P2**：fork WhisperLiveKit；在用户 GPU 上跑 large-v3-turbo + Sortformer；测首 partial p50/p95、final stable p50/p95、GPU 峰值
2. **写适配层 v0**：覆盖构造、流式喂数据、回调（含 diarization 原始 embedding 拿到）
3. **写 LICENSE 检查表**：WhisperLiveKit / faster-whisper / Sortformer / Diart / SpeechBrain / pyannote-audio / pgvector / structlog
4. **本地起 PG + pgvector**；schema v0 落盘；写 SQLite fallback 抽象
5. **写售前 GPU 调查清单 v0**（PDF/markdown）

### 决策节点（需要客户回答）

- **D-B（不变）**：客户 PG 能否装 pgvector？否则走 SQLite fallback
- **D-C（不变）**：前端 Demo 是否要作为客户集成参考？
- **D-D（新增）**：客户机房操作系统 + CUDA 版本？是否能装 cuDNN？
- **D-E（新增）**：合规边界确认 —— 上层会议系统承担用户同意取得 + 出境评估？写入合同模板？

### 长期方向

- 多卡 / 多机：先单例 + workers=1；伸缩时把推理层独立为 gRPC 后端
- 录播批转写：独立队列与 GPU 隔离
- 安全合规：等保 2.0 三级测评、国密 SM4 替换 AES、TLS 双向证书
- ITN / 数字归一化：v1.1 引入 wetext

---

## 11. 给用户的诚实话

v2 比 v1 多了 2 周工期，原因是 v1 把三件事低估了：

1. **WhisperLiveKit 不是稳定库**。要 fork。这是底线，不是过度工程
2. **真实会议室声纹注册拿不到 15s 干净样本**。承诺面要降，不然首家客户就翻车
3. **裸机部署到客户机房 0.5 周不够**。glibc / CUDA / cuDNN / Kylin / 离线 wheelhouse / Miniforge —— 任何一个踩雷都是一天。M5 给 1.5–2 周不是冗余，是兑现现实

护城河仍然在那些"算法栈不解决"的工程化、合规、运维细节里。但 v2 把这些细节明码标价写进了里程碑。

**保持 fork + 适配层路径不要动**。它给你两件别人没有的东西：
1. 进程内拿到原始 embedding（声纹匹配前提）
2. 进程内注入 initial_prompt（热词前提）

走网关或纯 import 库的人，要么永远拿不到 embedding，要么永远被上游 API 变更拖死。

**不要在 MVP 阶段写任何不在第 7 节里的代码**。M5 之前不动多机、不动 batch、不动 ITN、不动管理后台 UI。

---

## 12. 性能与质量验收数字（v2 新增）

### 12.1 延迟目标（large-v3-turbo + Sortformer，单租户单路）

| 指标 | 目标 | 实测方法 |
|---|---|---|
| 首 partial 延迟 | p50 ≤ 500ms，p95 ≤ 800ms | 从音频帧到达 server 起算，到首个 partial 发出 |
| final stable 延迟 | p50 ≤ 1500ms，p95 ≤ 2500ms | 从相关音频段结束到 final 发出 |
| 端到端 RTF（实时率） | ≤ 0.4 | 转写 1 秒音频耗时 ≤ 0.4 秒 |
| 心跳间隔 | 15s server 主动 ping | |

### 12.2 准确率目标

| 指标 | 目标 | 数据集 |
|---|---|---|
| CER（字错率） | ≤ 8% 干净录音，≤ 15% 真实会议 | AISHELL-4 抽样 + 自录 1 份真实会议 |
| SER（说话人错误率） | ≤ 15%（已注册者） | 同上 |
| 已注册说话人 top-1 匹配 | ≥ 80%（SNR ≥ 15dB） | M2a 验收集 |

### 12.3 资源占用目标

| 指标 | 目标 |
|---|---|
| GPU 显存峰值（单路） | ≤ 10GB |
| GPU 显存稳态（3 路） | ≤ 11GB |
| RSS 内存稳态 | ≤ 4GB |
| 6 小时长跑显存涨幅 | ≤ 5%（无 OOM） |

### 12.4 Prometheus 指标 schema

```
# session
meet_transcribe_active_sessions{tenant_id}
meet_transcribe_sessions_total{tenant_id,status}

# inference
meet_transcribe_inference_latency_seconds{stage="partial|final"}  histogram
meet_transcribe_audio_input_seconds_total{tenant_id}
meet_transcribe_words_emitted_total{tenant_id,type="partial|final"}

# speaker
meet_transcribe_speaker_match_total{tenant_id,result="hit|miss|anonymous"}
meet_transcribe_speaker_match_latency_seconds  histogram

# gpu
meet_transcribe_gpu_memory_used_bytes
meet_transcribe_gpu_utilization_ratio

# errors
meet_transcribe_errors_total{code,tenant_id}

# auth
meet_transcribe_auth_failures_total{reason}
meet_transcribe_ticket_consumed_total
```

---

## 13. 协议细节与错误码（v2 新增）

### 13.1 partial / final 回撤协议

- partial 携带 `seq`（单调递增）和 `stable_until`（之前的 seq 不再修改）
- 客户端规则：
  - 收到 partial 时按 seq 覆盖；
  - `seq <= stable_until` 的不会再变；
  - 收到 final 时该段 seq 区间锁定，后续不允许同 seq 的新 partial

### 13.2 错误码表

| code | HTTP/WS close code | 说明 | 用户可见消息 |
|---|---|---|---|
| AUTH_FAIL | 401 / 4401 | API Key 错或 ticket 失效 | 鉴权失败 |
| QUOTA_EXCEEDED | 429 / 4429 | 并发或日配额超限 | 配额已用完，请稍后 |
| RATE_LIMITED | 429 / 4429 | 短时间频繁建连 | 请求过于频繁 |
| AUDIO_FORMAT_INVALID | 4400 | 音频格式不符 | 不支持的音频格式 |
| RESUME_REQUIRED | 4408 | session 超时需重连 | 会话超时，请重新连接 |
| ENGINE_TIMEOUT | 4500 | 推理超时 | 转写引擎超时 |
| INTERNAL | 4500 | 内部错误 | 服务器内部错误 |

错误消息**不携带 traceback / 文件路径 / SQL 片段**；详细信息记入 audit_logs。

### 13.3 ITN / 标点 / 数字归一化（MVP 决策）

- MVP **关闭** ITN（"二零二六" 不自动改写为 "2026"）
- 标点恢复由 Whisper 模型原生输出
- 数字归一化 v1.1 引入（候选：wetext / FunASR ITN）

### 13.4 时间基

- session 起点 = WS 握手成功时刻 t0
- `start` / `end` / `words[i].start` / `words[i].end` 都是 t0 起算的相对秒（float）
- partial 与 final 的时间区间不重叠

---

## 14. 合规与数据生命周期（v2 新增）

### 14.1 数据分类

| 数据 | 分级 | 处理 |
|---|---|---|
| 声纹 embedding | **个人敏感信息（生物识别）** | speakers 表；consent_at 必填；删除即物理删除 + 重建索引 |
| 转写文本 | 一般个人信息 | transcripts.text_encrypted AES-GCM；按 tenants.data_retention_days 到期物理删除 |
| 原始音频 | 一般个人信息 | **MVP 不留存**；如客户要求留存，单独立配置项 |
| 审计日志 | 系统数据 | audit_logs 留存 ≥ 180 天（等保 2.0 三级） |

### 14.2 同意与删除（PIPL 第 17/29 条）

- `POST /v1/speakers` 必须带 `consent_source` 字段（如 "meeting_room_signage_v2"），写入 speakers.consent_source
- `DELETE /v1/speakers/{id}` 同步：删 speakers 行 → 删 HNSW 索引引用 → 删 LRU 缓存 → 写 audit_logs
- `DELETE /v1/sessions/{id}` 同步：物理删 transcripts 该 session 全部行 → 写 audit_logs
- 全量删除接口：`POST /v1/tenants/{id}/data/purge`，幂等，可恢复中断

### 14.3 加密

- 转写文本：AES-256-GCM，密钥来自 `MEET_TRANSCRIBE_DATA_KEY` 环境变量
- 国密替代：build flag `--with-sm4` 切换到 SM4-GCM（v1.1）
- 传输：仅支持 TLS 1.2+；HTTP 端点强制 HSTS

### 14.4 责任边界（合同模板要点）

| 责任 | 我方 | 上层会议系统 |
|---|---|---|
| 取得用户参会同意 | | ✓ |
| 取得声纹注册同意 | | ✓ |
| 数据加密存储 | ✓ | |
| 删除请求执行 | ✓（提供 API） | （触发删除） |
| 数据出境评估 | | ✓ |
| 审计日志 | ✓ | |
| 等保 2.0 三级测评 | ✓（产品侧） | ✓（系统侧） |

---

**Status**: v2 设计文档完成，吸收 35 条对抗式评审 findings；待用户最终确认或进入实施阶段。
