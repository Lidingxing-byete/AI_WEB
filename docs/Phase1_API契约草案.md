# Phase 1 交付物 ⑤ API 契约草案

> 证据标签：本文为 `[推断]`（接口设计草案），未与运行实现验证。
> 生成时间：2026-10-25。所属阶段：Phase 1。
> 协议：REST（JSON）+ SSE（诊断流式）。后端基址 `http://localhost:8000`。

---

## 一、通用约定

| 项 | 约定 |
|---|---|
| 认证 | 无（本地单用户） |
| 内容类型 | `application/json`；文件上传 `multipart/form-data` |
| 时间格式 | ISO 8601（UTC）字符串 |
| ID | UUID v4 字符串 |
| 错误响应 | `{ "error": { "code": "...", "message": "...", "detail": {...} } }` |
| 分页 | `?limit=&offset=`（列表接口） |

---

## 二、端点清单

| 方法 | 路径 | 说明 | 优先级 |
|---|---|---|---|
| GET | `/api/health` | 健康检查（含模型层状态） | P0 |
| GET | `/api/models/status` | 云端/本地/规则层可用状态 | P0 |
| POST | `/api/tasks` | 创建诊断任务 | P0 |
| GET | `/api/tasks` | 任务列表 | P0 |
| GET | `/api/tasks/{id}` | 任务详情 | P0 |
| PATCH | `/api/tasks/{id}` | 更新基础信息/状态 | P0 |
| DELETE | `/api/tasks/{id}` | 软删除 | P0 |
| POST | `/api/tasks/{id}/artifacts` | 提交材料（multipart 或 JSON） | P0 |
| GET | `/api/tasks/{id}/artifacts` | 材料列表 | P0 |
| GET | `/api/tasks/{id}/artifacts/{aid}` | 材料原文 | P0 |
| GET | `/api/tasks/{id}/diagnose` | **SSE**：触发并流式返回诊断过程 | P0 |
| GET | `/api/tasks/{id}/trace` | 完整 trace（步骤+工具调用） | P0 |
| GET | `/api/tasks/{id}/hypotheses` | 候选根因与证据 | P0 |
| GET | `/api/tasks/{id}/verifications` | 验证步骤清单 | P0 |
| POST | `/api/tasks/{id}/verifications/{sid}/result` | **回填实测结果** | P0 |
| GET | `/api/tasks/{id}/report` | 获取/生成诊断报告（Markdown） | P0 |
| GET | `/api/knowledge/cards` | 知识卡片列表 | P1 |
| POST | `/api/knowledge/search` | 检索测试（供评审核验） | P1 |

---

## 三、关键接口明细

### 3.1 `POST /api/tasks`

请求：
```json
{
  "title": "USART1 无输出排查",
  "chip_model": "STM32F103C8T6",
  "lib_type": "spl",
  "symptom_category": "S1",
  "symptom_desc": "上电后串口助手无任何输出，波特率 115200"
}
```
响应 `201`：
```json
{ "id": "t-8f2a…", "status": "draft", "created_at": "2026-10-25T04:00:00Z" }
```

### 3.2 `POST /api/tasks/{id}/artifacts`

支持两种形态：
- `multipart/form-data`：`file`（可选）、`kind`、`file_path`（代码类必填）
- `application/json`：`{ "kind": "description", "inline_content": "..." }`

响应 `201`：
```json
{ "id": "a-1c9…", "kind": "code", "parse_status": "pending", "size_bytes": 2048 }
```

校验错误 `400`：`FILE_TOO_LARGE` / `TYPE_NOT_ALLOWED` / `MISSING_FILE_PATH`（代码类未标注路径）。

### 3.3 `GET /api/tasks/{id}/diagnose`（SSE）⭐

触发诊断编排，流式返回结构化事件。
查询参数：`?last_event_id=`（断线重连增量）。

**事件格式**（每行 `event: <type>` + `data: <json>`）：

| 事件类型 | `data` 结构 | 说明 |
|---|---|---|
| `step_start` | `{ step_id, seq, name }` | 步骤开始 |
| `step_done` | `{ step_id, status, skip_reason? }` | 步骤结束/跳过 |
| `tool_call` | `{ step_id, inv_id, tool_name, input_summary }` | 工具调用开始 |
| `tool_result` | `{ inv_id, status, output_summary, deterministic, duration_ms, error_msg? }` | 工具返回 |
| `evidence_added` | `{ hypothesis_id, evidence: {...} }` | 新增证据 |
| `hypothesis_update` | `{ hypotheses: [...] }` | 候选根因新增/更新（含置信度与理由） |
| `retrieval_done` | `{ hits: [{hit_id, source_ref, score}] }` | 检索命中 |
| `model_status` | `{ provider, model_name, level, degraded }` | 模型层状态变化（降级时推送） |
| `token` | `{ text }` | 模型文本增量（仅用于说明性文字，非结论） |
| `done` | `{ status, hypothesis_count }` | 诊断结束 |
| `error` | `{ code, message, recoverable }` | 错误 |

示例流：
```
event: step_start
data: {"step_id":"s1","seq":1,"name":"编译日志解析"}

event: tool_call
data: {"step_id":"s1","inv_id":"i1","tool_name":"parse_build_log","input_summary":"build.log (1.2KB)"}

event: tool_result
data: {"inv_id":"i1","status":"ok","output_summary":"0 error, 2 warning","deterministic":true,"duration_ms":42}

event: hypothesis_update
data: {"hypotheses":[{"id":"h1","title":"USART1 波特率误差超出容差","confidence":0.72,"confidence_label":"较高","evidence_tag":"INFER","status":"active"}]}

event: done
data: {"status":"awaiting_verification","hypothesis_count":3}
```

### 3.4 `POST /api/tasks/{id}/verifications/{sid}/result`

请求：
```json
{
  "observed": "PA9 对 GND 用示波器测得无波形；改接 PA2 后出现波形",
  "match": "mismatch",
  "recorded_by": "Zero",
  "raw_ref": null
}
```
响应 `200`：
```json
{
  "test_result_id": "tr-…",
  "evidence_tag": "HW_TEST",
  "hypothesis_updates": [
    { "id": "h1", "from": 0.72, "to": 0.18, "status": "refuted", "reason": "实测未复现预期现象" }
  ],
  "task_status": "awaiting_verification"
}
```

> 服务端**不提供**自动填充 `observed` / `match` 的接口或默认值。缺失则 `422`。

### 3.5 `GET /api/tasks/{id}/report`

查询参数：`?regenerate=true`
响应：
```json
{ "id": "r-…", "version": 1, "generated_at": "…", "md_content": "# 诊断报告\n…" }
```

---

## 四、错误码

| HTTP | `code` | 场景 | 前端动作 |
|---|---|---|---|
| 400 | `FILE_TOO_LARGE` | 文件超限 | 提示上限，建议拆分 |
| 400 | `TYPE_NOT_ALLOWED` | 扩展名不在白名单 | 提示白名单 |
| 400 | `MISSING_FILE_PATH` | 代码类未标路径 | 要求补填 |
| 404 | `TASK_NOT_FOUND` / `STEP_NOT_FOUND` | 资源不存在 | 返回列表 |
| 409 | `INVALID_STATUS_TRANSITION` | 非法状态迁移 | 刷新任务状态 |
| 422 | `VALIDATION_FAILED` | 参数校验失败（含实测字段缺失） | 内联提示 |
| 503 | `MODEL_UNAVAILABLE` | 模型全不可用（已转规则层） | 顶部降级横幅 |
| 503 | `MODEL_TIMEOUT` | 模型超时 | 提示重试，保留已完成步骤 |
| 500 | `PARSE_FAILED` | 解析崩溃 | 标记步骤失败，继续其余 |
| 500 | `STORAGE_FAILED` | 存储写入失败 | 全局错误，任务标 `failed` |

---

## 五、安全相关约束（接口层）

1. **文件上传**：扩展名白名单 `.c .h .txt .log .md .map .png`（`.png` 仅 P1 波形图）；单文件与总量上限由配置控制。
2. **路径安全**：`artifacts` 读写限定在 `data/tasks/{task_id}/` 内，拒绝 `../` 与绝对路径。
3. **不执行代码**：无任何接口触发编译、运行或烧录。
4. **模型元数据**：`model_call` 不记录密钥；日志脱敏后写入。
5. **CORS**：仅允许本地前端源（`http://localhost:5173`）。

---

**Phase 1 交付物 ⑤ 结束。本文为接口设计草案，未编写任何后端代码。**
