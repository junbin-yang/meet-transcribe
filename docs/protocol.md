# meet-transcribe WebSocket 协议规范（草稿）

适用版本：v0.x（M0–M3）。M4 起作为对外正式契约固定。

源头：`docs/design-v2.md` 第 4.3、第 13 节。本文档把协议规范从设计文档单独拆出，便于上层会议系统集成方对接。

---

## 1. 鉴权

两步式：HTTP 换 ticket → WebSocket 携带 ticket。

### 1.1 HTTP 换 ticket

```
POST /v1/auth/ticket
Authorization: Bearer <api_key>
Content-Type: application/json

{ "session_hint": "meeting-room-7" }
```

返回：

```json
{ "ticket": "tk_xxxxxxxxxxxxxxxx", "expires_in": 30 }
```

ticket：一次性、TTL 30s、签名包含 `tenant_id` + 过期时间，HMAC-SHA256(MT_SERVER_SECRET)。

### 1.2 WebSocket 连接

```
GET /v1/ws/transcribe?ticket=tk_xxxxxxxxxxxxxxxx
Upgrade: websocket
```

握手时校验 ticket，**不接受** `Sec-WebSocket-Protocol: bearer.*` 携带 API Key（v1 设计已废弃，避免代理日志泄露）。

校验失败返回 4401，并通过 close frame 携带 `code=AUTH_FAIL`。

---

## 2. 帧格式

### 2.1 入站

第一帧：JSON 控制帧（文本帧）

```json
{
  "type": "start",
  "session_id": "optional-client-side-id",
  "language": "zh",
  "hotwords": ["千兆星瑞云"],
  "speaker_set": "meeting-room-7"
}
```

后续：二进制帧，16kHz mono PCM16 little-endian，每帧 ≤ 64KiB。

可选控制帧：

- `{"type":"pause"}` 暂停推理
- `{"type":"resume"}` 恢复
- `{"type":"end"}` 结束会话；服务端写完最后 final 后关连接
- `{"type":"hotwords","words":["..."]}` 会话中追加（M3 之后）

### 2.2 出站

#### partial（不稳定增量）

```json
{
  "type": "partial",
  "seq": 17,
  "stable_until": 12.34,
  "text": "我们下周要发布",
  "speaker": "S1",
  "speaker_label": "张三?",
  "confidence": 0.82
}
```

`stable_until`：服务端承诺该时间点之前的内容稳定，不会再回撤。客户端应按 `seq` 覆盖前一条 partial。

#### final（稳定）

```json
{
  "type": "final",
  "seq": 18,
  "start": 10.50,
  "end": 14.20,
  "text": "我们下周要发布千兆星瑞云。",
  "speaker": "S1",
  "speaker_label": "张三",
  "words": [
    { "w": "我们", "s": 10.50, "e": 10.86 },
    { "w": "下周", "s": 10.86, "e": 11.40 }
  ]
}
```

#### 心跳

```json
{ "type": "ping", "ts": "2026-05-25T11:30:00Z" }
```

服务端每 20s 发送一次；客户端可回 `{"type":"pong"}`。

#### 错误

```json
{ "type": "error", "code": "RATE_LIMITED", "message": "tenant concurrent limit reached" }
```

---

## 3. 错误码

| code | 含义 | 是否可恢复 |
|---|---|---|
| `AUTH_FAIL` | ticket 无效或过期 | 否，重新换 ticket |
| `RATE_LIMITED` | 租户并发或日配额超限 | 是，退避后重试 |
| `AUDIO_FORMAT_INVALID` | 采样率/声道/编码不符 | 否，客户端修 |
| `ENGINE_TIMEOUT` | 推理超时 | 是，自动重试 |
| `QUOTA_EXCEEDED` | 当日分钟数耗尽 | 否，等到次日或扩容 |
| `RESUME_REQUIRED` | 服务端断开后客户端要重连 | 是，发起新连接，复用 session_hint |
| `INTERNAL` | 兜底，不暴露内部细节 | 是，可重试 |

错误消息**不**包含 stacktrace、SQL 语句、内部路径。

---

## 4. 时间基

- `start` / `end` / `stable_until` 单位为秒，浮点。
- 起点 `t = 0` 是 WebSocket 收到第一个二进制音频帧的服务端时刻。
- 客户端如需挂钟时间，使用 `final.created_at_iso`（M2 之后追加），而不是自己计算。

---

## 5. ITN / 标点（MVP）

- ITN（中文数字归一）M0–M3 **关闭**。"二零二六年" 不会自动转成 "2026 年"。
- 标点由 Whisper 模型原生输出，质量随模型档位不同。
- v1.1 起评估接 paraformer 或 funasr 的标点恢复模块。

---

## 6. 不在协议中的行为（v0.x）

- 不支持音频压缩格式（Opus/AAC）：客户端必须先解到 PCM16
- 不支持 WebRTC：MVP 走原始 PCM over WebSocket
- 不支持多语言识别自动切换：start 帧的 `language` 决定整段会话
- 不返回声纹相似度分布，仅返回 `speaker_label` 与可选 `confidence`
