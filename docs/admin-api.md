# meet-transcribe 管理 API

运维人员使用，鉴权方式与客户 API 不同。

## 鉴权

所有管理端点通过 `X-Admin-Token` 请求头鉴权，值为环境变量 `MT_ADMIN_TOKEN`。

```
X-Admin-Token: <MT_ADMIN_TOKEN>
```

## 端点

Base: `http://<host>:<port>/v1/admin`

### 创建租户

```
POST /v1/admin/tenants
X-Admin-Token: <token>
Content-Type: application/json

{
  "name":                  "client-a",
  "quota_concurrent":      5,
  "quota_minutes_per_day": 600,
  "data_retention_days":   90
}
```

| 字段 | 说明 | 范围 |
|------|------|------|
| `name` | 租户名（唯一） | 1-128 chars |
| `quota_concurrent` | 最大并发会话数 | 1-64 |
| `quota_minutes_per_day` | 日转写分钟配额 | 1-10080 |
| `data_retention_days` | 数据保留天数 | 1-3650 |

返回 `{ id, name, quota_concurrent, quota_minutes_per_day, data_retention_days, created_at }`。

### 签发 API Key

```
POST /v1/admin/tenants/{tenant_id}/api-keys?label=prod
X-Admin-Token: <token>
```

| 参数 | 说明 |
|------|------|
| `tenant_id` | 租户 UUID（路径参数） |
| `label` | 可选标签（query 参数） |

返回：

```json
{
  "id": "uuid",
  "tenant_id": "uuid",
  "label": "prod",
  "api_key": "mt_...",
  "created_at": "2026-05-28T..."
}
```

**`api_key` 仅在创建时返回一次明文。** 数据库只存 HMAC-SHA256 哈希，事后无法查询。

## 错误响应

| code | HTTP | 含义 |
|------|------|------|
| `AUTH_FAIL` | 401 | `X-Admin-Token` 缺失或错误 |
| `VALIDATION_FAILED` | 422 | 租户名冲突或 tenant 不存在 |
| `INTERNAL` | 500 | 服务端未配置 `MT_ADMIN_TOKEN` |

## 交付流程

```
部署方:
  POST /v1/admin/tenants              → tenant_id
  POST /v1/admin/tenants/{id}/api-keys → api_key (mt_...)

交付给客户:
  api_key 字符串（唯一凭证）

客户使用:
  Authorization: Bearer <api_key>     → 服务端自动解析出 tenant
```
