# meet-transcribe API 对接文档

面向第三方会议系统集成方，描述 HTTP API 和 WebSocket 协议。

## 1. 鉴权

所有请求需携带 API Key：

```
Authorization: Bearer <api_key>
```

**API Key 是第三方客户接入所需的唯一凭证。** 无需 tenant ID、无需了解多租户结构。
客户从部署方获取一个 `mt_` 开头的 API Key 字符串即可开始对接。

API Key 由部署方通过管理接口签发，每个 tenant 可有多个 Key（不同客户端、不同环境）。

## 2. HTTP API

Base URL: `http://<host>:<port>/v1`

### 2.1 获取 WebSocket Ticket

```
POST /v1/auth/ticket
Authorization: Bearer <api_key>
Content-Type: application/json

{ "session_hint": "meeting-room-7" }
```

返回：

```json
{
  "ticket": "tk_xxxxxxxxxxxxxxxx",
  "expires_in": 300
}
```

ticket 一次性有效，TTL 300s。

### 2.2 离线文件转写

```
POST /v1/transcribe/file
Authorization: Bearer <api_key>
Content-Type: multipart/form-data

file:    <audio_file>        (WAV/FLAC/OGG/MP3/M4A)
language: "auto" | "zh" | "en"  (可选，默认 auto)
```

返回：

```json
{
  "text": "全文...",
  "duration_s": 18.2,
  "sentences": [
    {
      "text": "今天要聊什么呀？",
      "speaker": 1,
      "start": 0.7,
      "end": 4.9,
      "speaker_resolved": {
        "id": "uuid",
        "name": "张三",
        "score": 0.91
      }
    }
  ]
}
```

- `speaker`: 说话人编号（1-based），由 FunASR 自动聚类
- `speaker_resolved`: 如果该说话人匹配到已注册声纹，返回姓名和相似度
- `start` / `end`: 秒，从音频起始计算

### 2.3 声纹管理

#### 注册新声纹

```
POST /v1/speakers
Authorization: Bearer <api_key>
Content-Type: multipart/form-data

audio_file:     <wav/flac/ogg/mp3>  (>=15s, SNR>=15dB)
name:           "张三"
consent_source: "in_app_consent"     (可选)
```

返回 `{ id, name, sample_count, snr_db_avg, created_at }`。

#### 追加样本

```
POST /v1/speakers/{id}/samples
Authorization: Bearer <api_key>
Content-Type: multipart/form-data

audio_file: <wav/flac/ogg/mp3>
```

#### 查询声纹列表

```
GET /v1/speakers
Authorization: Bearer <api_key>
```

#### 删除声纹

```
DELETE /v1/speakers/{id}
Authorization: Bearer <api_key>
```

## 3. WebSocket 实时转写

```
GET /v1/ws/transcribe?ticket=<ticket>
Upgrade: websocket
```

### 3.1 入站帧

**第一帧**（JSON 文本帧）：

```json
{ "type": "start", "language": "auto" }
```

| 字段 | 说明 |
|------|------|
| `language` | `"auto"` / `"zh"` / `"en"` |

**后续帧**：二进制帧，16kHz mono PCM16 little-endian。

**结束帧**：

```json
{ "type": "end" }
```

### 3.2 出站帧

#### partial

每 500ms 推送一次，speaker 标签每 2s 更新一次。前端每次应**覆盖**上次 partial 内容。

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
      "speaker_resolved": { "id": "uuid", "name": "张三", "score": 0.91 }
    }
  ],
  "session_id": "uuid",
  "seq": 17
}
```

- `lines`: 说话人轮次数组，每项有独立 `text`
- `speaker`: 1-based speaker 编号，FunASR 自动聚类
- `speaker_resolved`: 声纹匹配结果，未匹配时不出现

#### error

```json
{ "type": "error", "code": "ENGINE_TIMEOUT", "message": "engine failed" }
```

### 3.3 时序

```
Client                          Server
  |-- POST /v1/auth/ticket ----->|
  |<-- { ticket } ---------------|
  |-- WS connect?ticket=... ---->|
  |-- { type:"start" } --------->|
  |-- <binary audio> ----------->|  (持续发送)
  |<-- { type:"partial" } -------|  (每 500ms)
  |<-- { type:"partial" } -------|  (每 2s 带 speaker 标签)
  |-- { type:"end" } ----------->|
  |<-- { type:"partial" } -------|  (最终结果)
  |<-- WS close -----------------|
```

## 4. 错误响应

HTTP 错误格式：

```json
{ "detail": { "code": "AUTH_FAIL", "message": "invalid api key" } }
```

| code | HTTP | 含义 |
|------|------|------|
| `AUTH_FAIL` | 401 | API Key 无效或 ticket 过期 |
| `VALIDATION_FAILED` | 422 | 音频质量不达标 |
| `ENGINE_TIMEOUT` | 500 | 推理超时 |
| `INTERNAL` | 500 | 内部错误 |

## 5. 音频要求

| 参数 | 实时流 | 离线文件 |
|------|--------|---------|
| 采样率 | 16000 Hz | 16000 Hz |
| 声道 | 单声道 | 单声道 |
| 编码 | PCM int16 | WAV/FLAC/OGG/MP3/M4A |
| 声纹注册 | >=15s, SNR>=15dB | >=15s, SNR>=15dB |
