# VidGen 第三方开发者 API 接入文档

本文档面向两类读者：

- 平台方：你自己或你的运营/交付同学，负责创建并发放 API Key
- 第三方开发者：调用 VidGen 外部视频生成 API 的合作方

## 1. 先说结论

- 给第三方调用的业务 API 是 `/v1/video-jobs*`
- 第三方调用使用 `Authorization: Bearer vg_...` 鉴权
- `vg_...` 类型的 key 需要由 VidGen 平台侧创建
- 当前前端已提供独立管理页：
  - 普通用户：`仪表盘 -> API Keys -> 我的密钥`
  - 管理员：`仪表盘 -> API Keys -> 客户密钥`
- 如需脚本化创建，也可以继续调用后端接口：
  - `POST /api/api-keys`
  - `POST /api/admin/api-keys`

请注意：

- `.env` 里的上游模型厂商密钥，例如 `sk-...`，不是给第三方调用 VidGen 的 API Key
- 真正发给第三方的，是 VidGen 创建出来的 `vg_...` Key

## 2. API 基础信息

- Base URL：
  - 本地开发：`http://localhost:8000`
  - 生产环境：替换为你的正式域名，例如 `https://api.example.com`
- 文档入口：
  - OpenAPI JSON：`GET /openapi.json`
  - Swagger UI：`GET /docs`
- 健康检查：
  - `GET /api/health`

## 3. 平台方：在哪里创建 API Key

### 3.0 通过前端界面创建（推荐）

1. 登录 VidGen 平台
2. 进入任意项目
3. 点击右上角 `仪表盘`
4. 切换到 `API Keys` 页签
5. 如果你是普通用户：
   - 停留在 `我的密钥`
   - 填写名称
   - 勾选权限
   - 点击 `创建 API Key`
6. 如果你是管理员，要给客户单独发 key：
   - 切到 `客户密钥`
   - 先选择目标客户账号
   - 填写名称
   - 勾选权限
   - 点击 `为客户创建 API Key`
7. 创建成功后，界面会弹出完整 `vg_...` key
8. 立即复制并通过安全渠道发给客户
9. 如果后续不再使用，回到列表点击 `停用`

注意：

- 完整 key 只在创建成功当下展示一次
- 列表页只会保留前缀、状态、权限和最后使用时间
- 当前采用“停用”替代物理删除，避免把历史外部任务映射一起清掉

### 3.1 当前用户给自己创建

接口：

- `POST /api/api-keys`

认证方式：

- 需要先登录 VidGen，使用登录态 Cookie 调用

如果你还没有登录态，可以先调用：

- `POST /api/auth/login`

登录示例：

```bash
curl -i -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "YOUR_USERNAME",
    "password": "YOUR_PASSWORD"
  }'
```

成功后响应头里会带 `Set-Cookie: vidgen_session=...`，后续创建 API Key 时带上这个 Cookie 即可。

请求体：

```json
{
  "name": "partner integration",
  "scopes": ["video_jobs:create", "video_jobs:read", "video_jobs:review"]
}
```

说明：

- `name` 是这把 key 的备注名
- `scopes` 可选，默认就是：
  - `video_jobs:create`
  - `video_jobs:read`
  - `video_jobs:review`

创建示例：

```bash
curl -X POST http://localhost:8000/api/api-keys \
  -H "Content-Type: application/json" \
  --cookie "vidgen_session=YOUR_SESSION_COOKIE" \
  -d '{
    "name": "partner integration",
    "scopes": ["video_jobs:create", "video_jobs:read", "video_jobs:review"]
  }'
```

成功响应示例：

```json
{
  "id": "0f18d45a-xxxx-xxxx-xxxx-2c5c7d5c2a7c",
  "name": "partner integration",
  "key_prefix": "vg_xxxxxxxxx",
  "status": "active",
  "scopes": [
    "video_jobs:create",
    "video_jobs:read",
    "video_jobs:review"
  ],
  "last_used_at": null,
  "created_at": "2026-04-23T10:00:00Z",
  "api_key": "vg_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

注意：

- 明文 `api_key` 只会在创建时返回一次
- 服务端只保存哈希，不会再次返回完整 key
- 你应当在创建后立即保存，并安全地发给对应合作方

### 3.2 管理员为指定用户创建

接口：

- `POST /api/admin/api-keys`

请求体：

```json
{
  "user_id": "TARGET_USER_ID",
  "name": "customer-a-prod",
  "scopes": ["video_jobs:create", "video_jobs:read", "video_jobs:review"]
}
```

适用场景：

- 你想把某个合作方的所有任务归属到单独账号
- 你需要做按客户隔离、审计和禁用

### 3.3 查看和禁用 API Key

- `GET /api/api-keys`
- `POST /api/api-keys/{api_key_id}/disable`
- `GET /api/admin/api-keys`
- `POST /api/admin/api-keys/{api_key_id}/disable`

建议：

- 每个合作方单独一把 key，不要多人共用
- 测试环境和生产环境分开创建
- 泄漏或停用合作时，直接禁用对应 key

## 4. 第三方开发者：如何调用 API

### 4.1 鉴权方式

所有外部业务请求都使用：

```http
Authorization: Bearer vg_xxx
```

### 4.2 可用接口一览

- `POST /v1/video-jobs`
  - 创建视频任务
- `GET /v1/video-jobs/{job_id}`
  - 查询任务状态
- `GET /v1/video-jobs/{job_id}/events`
  - 通过 SSE 流式获取任务进度
- `POST /v1/video-jobs/{job_id}/review`
  - 审核分镜或复刻方案后继续执行
- `GET /v1/video-jobs/{job_id}/result`
  - 下载完成后的 mp4

## 5. 创建任务

### 5.1 请求说明

接口：

- `POST /v1/video-jobs`

请求类型：

- `multipart/form-data`

表单字段：

- `spec`
  - 必填
  - JSON 字符串
- `images`
  - 必填
  - 1 到 100 张图片
- `reference_video`
  - 可选
  - 如果传入，会进入“复刻方案审核”链路
- `watermark`
  - 可选
  - 作为水印素材导入

请求头：

- `Authorization: Bearer vg_...`
- `Idempotency-Key: your-order-id`
  - 可选但强烈建议
  - 用于幂等去重，避免重复下单

### 5.2 `spec` 字段说明

`spec` 需要是一个 JSON 对象，当前支持字段如下：

```json
{
  "prompt": "用这些素材生成一条抖音营销视频",
  "script": "",
  "platform": "douyin",
  "duration_seconds": 30,
  "duration_mode": "fixed",
  "style": "commercial",
  "generation_model": "seedance1.5-pro",
  "video_model_no_audio": true,
  "voiceover_no_audio": true,
  "voice_id": "default",
  "transition": "none",
  "transition_duration": 0.5,
  "bgm_mood": "none",
  "bgm_volume": 0.15,
  "client_reference_id": "customer-order-123"
}
```

字段约束：

- `prompt` 和 `script` 至少要提供一个
- `platform` 可选值：
  - `douyin`
  - `xiaohongshu`
  - `bilibili`
  - `generic`
- `duration_seconds` 范围：`1-300`
- `style` 可选值：
  - `commercial`
  - `lifestyle`
  - `cinematic`
- `generation_model` 可选值：
  - `seedance1.5-pro`
  - `seedance2.0`
  - `kling`
  - `mock`

### 5.3 curl 示例

```bash
curl -X POST http://localhost:8000/v1/video-jobs \
  -H "Authorization: Bearer vg_xxx" \
  -H "Idempotency-Key: customer-order-123" \
  -F 'spec={
    "prompt":"用这些素材生成一条抖音大健康营销视频",
    "platform":"douyin",
    "duration_seconds":30,
    "style":"commercial",
    "client_reference_id":"customer-order-123"
  }' \
  -F "images=@./image-1.png" \
  -F "images=@./image-2.png"
```

### 5.4 JavaScript 示例

```js
const form = new FormData();
form.append(
  "spec",
  JSON.stringify({
    prompt: "用这些素材生成一条抖音大健康营销视频",
    platform: "douyin",
    duration_seconds: 30,
    style: "commercial",
    client_reference_id: "customer-order-123",
  }),
);
form.append("images", file1);
form.append("images", file2);

const response = await fetch("https://api.example.com/v1/video-jobs", {
  method: "POST",
  headers: {
    Authorization: "Bearer vg_xxx",
    "Idempotency-Key": "customer-order-123",
  },
  body: form,
});

const data = await response.json();
console.log(data);
```

### 5.5 创建成功响应示例

```json
{
  "job_id": "2f6d6c96-xxxx-xxxx-xxxx-1f1cd5d3e9d8",
  "status": "queued",
  "internal_status": "pending",
  "current_agent": null,
  "review": {
    "type": null,
    "required": false,
    "data": {}
  },
  "result": {
    "download_url": null
  },
  "error": null,
  "created_at": "2026-04-23T10:05:00Z",
  "updated_at": "2026-04-23T10:05:00Z"
}
```

### 5.6 Python 完整调用示例（含输入 / 输出）

下面给出一个可直接运行的 Python 示例。它会：

1. 组装输入参数
2. 调用 `POST /v1/video-jobs`
3. 打印 HTTP 状态码和返回 JSON

运行前准备：

```bash
pip install requests
```

输入示例：

```python
BASE_URL = "http://localhost:8000"
API_KEY = "vg_xxx"
IMAGE_PATHS = ["./image-1.png", "./image-2.png"]

SPEC = {
    "prompt": "用这些素材生成一条抖音大健康营销视频",
    "platform": "douyin",
    "duration_seconds": 30,
    "style": "commercial",
    "client_reference_id": "customer-order-123",
}
```

调用代码：

```python
import json
import requests

BASE_URL = "http://localhost:8000"
API_KEY = "vg_xxx"
IMAGE_PATHS = ["./image-1.png", "./image-2.png"]

SPEC = {
    "prompt": "用这些素材生成一条抖音大健康营销视频",
    "platform": "douyin",
    "duration_seconds": 30,
    "style": "commercial",
    "client_reference_id": "customer-order-123",
}

url = f"{BASE_URL}/v1/video-jobs"
headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Idempotency-Key": "customer-order-123",
}
data = {
    "spec": json.dumps(SPEC, ensure_ascii=False),
}

files = []
opened_files = []

try:
    for image_path in IMAGE_PATHS:
        file_obj = open(image_path, "rb")
        opened_files.append(file_obj)
        files.append(("images", (image_path.split("/")[-1], file_obj, "image/png")))

    response = requests.post(url, headers=headers, data=data, files=files, timeout=120)

    print("HTTP Status:", response.status_code)
    print("Response JSON:")
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
finally:
    for file_obj in opened_files:
        file_obj.close()
```

输出示例：

```text
HTTP Status: 202
Response JSON:
{
  "job_id": "2f6d6c96-xxxx-xxxx-xxxx-1f1cd5d3e9d8",
  "status": "queued",
  "internal_status": "pending",
  "current_agent": null,
  "review": {
    "type": null,
    "required": false,
    "data": {}
  },
  "result": {
    "download_url": null
  },
  "error": null,
  "created_at": "2026-04-23T10:05:00Z",
  "updated_at": "2026-04-23T10:05:00Z"
}
```

如果你想继续查询状态，可直接复用返回里的 `job_id`：

```python
job_id = response.json()["job_id"]
status_resp = requests.get(
    f"{BASE_URL}/v1/video-jobs/{job_id}",
    headers={"Authorization": f"Bearer {API_KEY}"},
    timeout=30,
)
print(json.dumps(status_resp.json(), ensure_ascii=False, indent=2))
```

## 6. 查询状态

接口：

- `GET /v1/video-jobs/{job_id}`

示例：

```bash
curl http://localhost:8000/v1/video-jobs/JOB_ID \
  -H "Authorization: Bearer vg_xxx"
```

外部可见状态：

- `queued`
- `processing`
- `requires_review`
- `completed`
- `failed`
- `cancelled`

当 `status=requires_review` 时，查看响应里的：

- `review.type`
  - `shot_plan`
  - `replication_plan`
- `review.required`
- `review.data`

说明：

- 普通生成通常会停在 `shot_plan`
- 带参考视频的任务通常会停在 `replication_plan`
- 审核数据已做脱敏，不返回本机文件绝对路径和敏感 token

## 7. 流式获取进度

接口：

- `GET /v1/video-jobs/{job_id}/events`

说明：

- 使用 SSE
- 服务端会周期性推送任务状态和 agent 进度
- 终态时会发出 `done` 事件

注意：

- 这个接口要求 `Authorization: Bearer vg_...`
- 浏览器原生 `EventSource` 不能直接附带自定义 `Authorization` header
- 因此更推荐两种方式：
  - 服务端消费 SSE，再转发给前端
  - 直接轮询 `GET /v1/video-jobs/{job_id}`

curl 示例：

```bash
curl -N http://localhost:8000/v1/video-jobs/JOB_ID/events \
  -H "Authorization: Bearer vg_xxx"
```

## 8. 审核并继续执行

接口：

- `POST /v1/video-jobs/{job_id}/review`

请求体：

```json
{
  "approved": true,
  "adjustments": "如不通过时，可填写调整建议",
  "edited_shots": [
    {
      "shot_idx": 0,
      "script_segment": "新的口播内容",
      "video_prompt": "新的镜头提示词",
      "duration_seconds": 6
    }
  ]
}
```

说明：

- 当 `review.type=shot_plan` 时：
  - 可直接 `approved=true`
  - 也可以在 `edited_shots` 中修改分镜后再批准
- 当 `review.type=replication_plan` 时：
  - `approved=true` 表示确认方案继续执行
  - `approved=false` 时，可通过 `adjustments` 提交修改意见

成功响应示例：

```json
{
  "status": "confirmed",
  "message": "Shot plan approved; video generation resumed"
}
```

## 9. 下载结果

接口：

- `GET /v1/video-jobs/{job_id}/result`

示例：

```bash
curl -L http://localhost:8000/v1/video-jobs/JOB_ID/result \
  -H "Authorization: Bearer vg_xxx" \
  -o result.mp4
```

说明：

- 只有任务 `completed` 后才能下载
- 如果任务还未完成，会返回 `409`
- 成功时返回 `video/mp4`

## 10. 常见错误码

- `400`
  - 请求参数错误
  - 上传文件数量或格式不合法
- `401`
  - 未提供 API Key
  - API Key 格式错误或无效
- `403`
  - API Key 已禁用
  - API Key scope 不足
- `404`
  - `job_id` 不存在
  - 任务不属于当前 API Key
- `409`
  - 任务尚未完成，暂时不能下载结果
- `422`
  - `spec` 字段结构不符合约束
- `500`
  - 服务内部错误

## 11. 推荐接入方式

建议第三方这样对接：

1. 服务端保存 `vg_...` Key，不要直接暴露到浏览器前端
2. 创建任务时始终传 `Idempotency-Key`
3. 用 `client_reference_id` 保存你方订单号或业务单号
4. 创建任务后优先使用轮询或 SSE 跟踪状态
5. 当收到 `requires_review` 时，进入人工审核或业务审批环节
6. 下载完成视频后，再由你方系统二次分发或归档

## 12. 安全建议

- 不要把 VidGen API Key 写进公开前端代码
- 每个合作方单独发一把 Key
- 测试环境和生产环境分离
- 如果怀疑泄漏，立即禁用并重发
- 如果你用反向代理对外发布，建议：
  - 只暴露 `/v1/*`
  - 开启 HTTPS
  - 增加限流、审计日志和 WAF

## 13. 平台方向合作方交付时，建议一并提供

- 生产环境 Base URL
- 一把独立的 `vg_...` API Key
- 一个最小可运行的 curl 示例
- 你要求他们传入的 `client_reference_id` 规范
- 审核链路说明：
  - 是否需要人工审核
  - 谁来调用 `/review`
  - 失败重试规则

## 14. 最小对接清单

第三方至少需要完成以下 4 步：

1. 保存你发放的 `vg_...` API Key
2. 调用 `POST /v1/video-jobs` 创建任务
3. 调用 `GET /v1/video-jobs/{job_id}` 或 SSE 跟踪状态
4. 在任务完成后调用 `GET /v1/video-jobs/{job_id}/result` 下载视频
