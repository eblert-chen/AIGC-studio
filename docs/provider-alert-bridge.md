# Provider 告警接收与转发

这条链路用于把 new-api Relay 的 Provider 故障和恢复事件送到公司自己的值班系统：

```text
new-api Provider Monitor
  -> 持久化告警事件与 Relay 重试队列
  -> POST /internal/relay/provider-alerts
  -> Platform 验签、时间窗校验、事件幂等
  -> 公司自有告警 Webhook
```

它不会把“配置为空”当作可用。Platform 生产环境缺少任一入站密钥、下游 URL 或出站密钥都会拒绝启动；开发环境未配置时，接收端返回 `503`。

## 1. Relay 到 Platform

生产地址固定为：

```text
https://<Platform API 域名>/internal/relay/provider-alerts
```

Relay 必须发送原始 JSON 字节和以下请求头：

```text
X-Relay-Event-ID: <canonical UUID>
X-Relay-Timestamp: <Unix seconds>
X-Relay-Signature: v1=<lowercase hex HMAC-SHA256>
```

签名输入不能重新序列化：

```text
timestamp + "." + event_id + "." + exact_raw_body
```

Platform 使用 `PROVIDER_ALERT_SIGNING_SECRET` 验证签名，默认只接受前后 300 秒内的时间戳，同时拒绝重复 JSON key、未知字段、事件头/body ID 不一致、事件类型和 incident 状态不一致。

同一个 `event_id + payload` 已成功转发时返回 `200`，不重复触发下游；同一个 `event_id` 绑定不同 payload 时返回 `409`。下游失败时不会留下“已成功”的幂等回执，而是返回 `503`，让 Relay 的持久队列继续重试。

## 2. Platform 到值班系统

Platform 把收到的原始 JSON 字节原样转发，并附带：

```text
Idempotency-Key: <event_id>
X-Alert-Event-ID: <event_id>
X-Alert-Timestamp: <Unix seconds>
X-Alert-Signature: v1=<lowercase hex HMAC-SHA256>
X-Request-ID: <Platform request id>
```

出站签名算法与入站相同，但必须使用独立的 `PROVIDER_ALERT_FORWARD_SIGNING_SECRET`。客户端不跟随重定向、不读取系统代理；任何非 `2xx` 或网络错误都会变成给 Relay 的 `503`。

Relay 到 Platform、Platform 到下游都是至少一次投递。极端情况下，下游已经返回 `2xx`、Platform 尚未提交回执就崩溃，Relay 会再次发送。因此值班系统必须用 `Idempotency-Key` 去重，不能假设事件只收到一次。

## 3. 服务端配置

以下值只放服务器密钥管理器，不进入前端：

```text
# new-api Relay
NEW_API_RELAY_PROVIDER_ALERT_WEBHOOK_URL=https://api.example.com/internal/relay/provider-alerts
NEW_API_RELAY_PROVIDER_ALERT_SIGNING_SECRET=<随机入站密钥，至少 32 UTF-8 字节>

# Platform；Compose 会把上面的 new-api 密钥映射为入站密钥
PROVIDER_ALERT_SIGNATURE_MAX_AGE_SECONDS=300
PROVIDER_ALERT_FORWARD_WEBHOOK_URL=https://alerts.example.com/provider-events
PROVIDER_ALERT_FORWARD_SIGNING_SECRET=<另一把随机出站密钥，至少 32 UTF-8 字节>
PROVIDER_ALERT_FORWARD_TIMEOUT_SECONDS=5
```

直接部署 Platform、不使用仓库 Compose 时，还要显式设置：

```text
PROVIDER_ALERT_SIGNING_SECRET=<与 new-api Relay 一致的入站密钥>
```

生产下游 URL 只允许规范化的公网 HTTPS 443 地址，禁止用户名/密码、query、fragment、localhost、私网 IP 和重定向。两把告警密钥还必须与 Relay API、内部服务、回调、遥测、成本和下载密钥全部不同。

## 4. 仍需外部准备

用户需要提供的非秘密信息：

- Platform 的正式 HTTPS API 域名；
- 公司值班 Webhook 的 HTTPS 地址；
- 值班系统负责人和一条测试告警的确认窗口。

需要由运维人员直接放入服务器密钥管理器、不要发到聊天里的秘密：

- Relay 到 Platform 的随机 HMAC 密钥；
- Platform 到值班系统的另一把随机 HMAC 密钥。

如果最终下游是飞书、企业微信、钉钉、PagerDuty 或华为云 SMN，而它不能直接接收本文的 JSON/HMAC 契约，需要在下游地址处部署对应的小型格式适配器；在该适配器和真实收件人完成一次触发、一次恢复、一次失败重试演练之前，告警链路不能标记为生产可用。
