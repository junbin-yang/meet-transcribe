# meet-transcribe WebSocket 协议规范

版本：v1.0 | 对外正式契约 | 2026-05-28

## 1. 鉴权

两步式：HTTP 换 ticket → WebSocket 携带 ticket。

### 1.1 换取 ticket

```
POST /v1/auth/ticket
Authorization: Bearer <api_key>
Content-Type: application/json

{ "session_hint": "meeting-room-7" }
```

返回：

```json
{ "ticket": "tk_xxxxxxxxxxxxxxxx", "expires_in": 300 }
```

ticket 一次性，TTL 300s，HMAC-SHA256(MT_SERVER_SECRET) 签名，内含 tenant_id + 过期时间。

### 1.2 WebSocket 连接

```
GET /v1/ws/transcribe?ticket=tk_xxxxxxxxxxxxxxxx
Upgrade: websocket
```

握手时校验 ticket。失败返回 `code=AUTH_FAIL`。

## 2. 入站帧

**第一帧** — JSON 控制帧：

```json
{
  "type": "start",
  "language": "auto",
  "model": null
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `type` | 是 | `"start"` |
| `language` | 否 | `"auto"` / `"zh"` / `"en"`，默认 `"auto"` |
| `model` | 否 | 覆盖配置的模型档位，`null` 使用默认 |

**后续帧** — 二进制帧：16kHz mono PCM16 little-endian，每帧任意大小。

**结束帧** — JSON 控制帧：

```json
{ "type": "end" }
```

## 3. 出站帧

### partial（增量结果）

每 500ms 推送一次，speaker 标签每 2s 更新一次。客户端应**覆盖**上次 partial。

```json
{
  "type": "partial",
  "text": "全文累积...",
  "lines": [
    {
      "text": "今天要聊什么呀？",
      "speaker": 2,
      "start": 7.4,
      "end": 9.1,
      "speaker_resolved": {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "name": "张三",
        "score": 0.9141
      }
    },
    {
      "text": "你对电影有什么...",
      "speaker": 1,
      "start": 10.4,
      "end": 14.2
    }
  ],
  "session_id": "uuid",
  "seq": 17
}
```

| 字段 | 说明 |
|------|------|
| `text` | 全文（累积） |
| `lines` | 说话人轮次数组 |
| `lines[].text` | 该轮次文本 |
| `lines[].speaker` | 1-based speaker 编号 |
| `lines[].start` / `end` | 秒，从会话起始计算 |
| `lines[].speaker_resolved` | 声纹匹配结果，未匹配时不出现 |
| `session_id` | 会话 UUID |
| `seq` | 递增序号 |

### error

```json
{ "type": "error", "code": "ENGINE_TIMEOUT", "message": "engine failed" }
```

## 4. 错误码

| code | 含义 | 可恢复 |
|------|------|--------|
| `AUTH_FAIL` | ticket 无效或过期 | 否 |
| `ENGINE_TIMEOUT` | 推理超时 | 是 |
| `INTERNAL` | 内部错误 | 是 |

## 5. 时间基准

- `start` / `end` 单位秒，浮点
- `t = 0` 为服务端收到第一个二进制音频帧的时刻

## 6. 音频要求

| 参数 | 值 |
|------|-----|
| 采样率 | 16000 Hz |
| 声道 | mono |
| 编码 | PCM int16 LE |
| 帧大小 | 任意（推荐 < 64KB） |

## 7. 说话人标签更新时序

- 0-6s：`speaker` 均为 1（VAD 积累中）
- 首次 SPK 完成后（6-10s）：出现多 speaker 标签
- 每 2s 刷新标签
- `speaker_resolved` 在声纹匹配后出现（≥ 3s 音频 + pgvector）

## 8. 不在协议中（v1.0）

- 不支持音频压缩格式 — 客户端先解到 PCM16
- 不支持 WebRTC
- 不支持 pause / resume
- 不返回 word-level 时间戳
