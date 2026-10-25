# Phase 1 交付物 ④ 数据模型草案

> 证据标签：本文为 `[推断]`（数据设计推导），未与真实数据验证。
> 生成时间：2026-10-25。所属阶段：Phase 1。
> 存储载体：**SQLite**（单文件），Phase 3 实施时以 SQL 迁移脚本落地。

---

## 一、实体总览

| # | 实体 | 表名 | 说明 |
|---|---|---|---|
| 1 | 诊断任务 | `task` | 一次诊断的聚合根 |
| 2 | 材料 | `artifact` | 用户提交的代码/日志/现象 |
| 3 | 工具调用记录 | `tool_invocation` | trace 核心：每次工具调用的输入输出 |
| 4 | 诊断步骤 | `diagnosis_step` | 编排器的步骤序列 |
| 5 | 候选根因 | `hypothesis` | 一个可能的故障原因 |
| 6 | 证据 | `evidence` | 支持/反对某假设的具体依据 |
| 7 | 验证步骤 | `verification_step` | 建议用户执行的上板操作 |
| 8 | 实测结果 | `test_result` | 用户回填的真实硬件结果 |
| 9 | 模型调用记录 | `model_call` | 模型名、参数、耗时、错误（不含密钥） |
| 10 | 检索记录 | `retrieval_record` | RAG 命中条目与来源 |
| 11 | 诊断报告 | `report` | 终版报告快照 |
| 12 | 状态变更日志 | `task_event` | 任务状态迁移历史 |

**关系**：`task` 1—N `artifact` / `diagnosis_step` / `hypothesis` / `verification_step` / `model_call` / `retrieval_record` / `task_event`；`diagnosis_step` 1—N `tool_invocation`；`hypothesis` 1—N `evidence` / `verification_step`；`verification_step` 1—1 `test_result`；`task` 1—1 `report`。

---

## 二、表结构草案

### 1. `task` 诊断任务

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT (UUID) PK | 任务 ID |
| `title` | TEXT NOT NULL | 任务标题 |
| `chip_model` | TEXT NOT NULL | 默认 `STM32F103C8T6` |
| `lib_type` | TEXT NOT NULL | 枚举：`spl` / `hal` / `unknown` |
| `symptom_category` | TEXT | 枚举：`S1..S6`（见 PRD 2.2） |
| `symptom_desc` | TEXT | 用户自由描述 |
| `status` | TEXT NOT NULL | 枚举见 §3.1 |
| `degraded` | INTEGER DEFAULT 0 | 是否处于降级模式 |
| `created_at` / `updated_at` | TEXT (ISO8601) | 时间戳 |
| `resolved_hypothesis_id` | TEXT NULL | 被实测确认的假设 ID |

### 2. `artifact` 材料

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT PK | |
| `task_id` | TEXT FK | |
| `kind` | TEXT | 枚举：`code` / `build_log` / `serial_log` / `description` / `waveform`(P1) |
| `name` | TEXT | 显示名 |
| `file_path` | TEXT NULL | 用户标注的工程内路径（代码类必填） |
| `content_ref` | TEXT | 落盘相对路径（大内容不入库） |
| `inline_content` | TEXT NULL | 短内容直接入库 |
| `size_bytes` | INTEGER | 用于超限校验 |
| `parse_status` | TEXT | `pending` / `ok` / `failed` |
| `parse_error` | TEXT NULL | 失败原因（E-03 用） |

### 3. `diagnosis_step` 诊断步骤

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT PK | |
| `task_id` | TEXT FK | |
| `seq` | INTEGER | 步骤序号 |
| `name` | TEXT | 如「编译日志解析」 |
| `status` | TEXT | `pending`/`running`/`ok`/`failed`/`skipped` |
| `skip_reason` | TEXT NULL | 跳过原因（体现"不为展示而调工具"） |
| `started_at` / `ended_at` | TEXT | |

### 4. `tool_invocation` 工具调用

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT PK | |
| `step_id` | TEXT FK | |
| `tool_name` | TEXT NOT NULL | 六个工具之一 |
| `input_json` | TEXT | 入参快照（脱敏后） |
| `output_json` | TEXT | 出参快照 |
| `status` | TEXT | `ok` / `failed` / `timeout` |
| `error_msg` | TEXT NULL | |
| `duration_ms` | INTEGER | |
| `deterministic` | INTEGER DEFAULT 1 | 1=确定性结果；0=含模型推断 |

### 5. `hypothesis` 候选根因

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT PK | |
| `task_id` | TEXT FK | |
| `title` | TEXT NOT NULL | 如「USART1 波特率误差超出容差」 |
| `detail` | TEXT | 说明 |
| `confidence` | REAL | 0–1，两位小数 |
| `confidence_label` | TEXT | `较高`/`中等`/`较低`（防虚假精确） |
| `evidence_tag` | TEXT NOT NULL | 六级标签之一 |
| `status` | TEXT | `active` / `refuted` / `confirmed` |
| `rank` | INTEGER | 排序 |
| `created_at` / `updated_at` | TEXT | |

### 6. `evidence` 证据

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT PK | |
| `hypothesis_id` | TEXT FK | |
| `polarity` | TEXT NOT NULL | `support` / `against` |
| `evidence_tag` | TEXT NOT NULL | 六级标签 |
| `source_type` | TEXT | `tool` / `knowledge` / `user_test` / `model` |
| `source_ref` | TEXT NULL | 如工具名+调用 ID，或 `RM0008 Ch.19` |
| `quote` | TEXT | 引用片段（代码行/日志行/知识卡片标题） |
| `location` | TEXT NULL | 文件:行号 |
| `note` | TEXT | 说明 |

> 强制约束：`evidence_tag` 非空。写入时校验缺失即报错（对应 UI 红色告警）。

### 7. `verification_step` 验证步骤

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT PK | |
| `hypothesis_id` | TEXT FK NULL | 可为通用检查（不绑定假设） |
| `seq` | INTEGER | |
| `action` | TEXT NOT NULL | 操作描述 |
| `measure_point` | TEXT | 测量点（如 PA9 对 GND） |
| `expected` | TEXT | **预期结果**（由系统生成） |
| `tools_needed` | TEXT | 所需工具：`stlink`/`serial`/`multimeter`/`scope` |
| `safety_note` | TEXT NULL | 接线/供电提醒 |

### 8. `test_result` 实测结果

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT PK | |
| `step_id` | TEXT FK | |
| `observed` | TEXT NOT NULL | 用户填写的实测现象 |
| `match` | TEXT NOT NULL | `match` / `mismatch` / `uncertain` |
| `raw_ref` | TEXT NULL | 截图/原始记录文件 |
| `evidence_tag` | TEXT NOT NULL DEFAULT `[实测-硬件]` | |
| `recorded_by` | TEXT | 记录人（用户自填） |
| `recorded_at` | TEXT | 记录时间 |

> **红线**：`observed` 与 `match` 只能由用户提交，服务端不提供任何自动填充接口。

### 9. `model_call` 模型调用

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT PK | |
| `task_id` | TEXT FK | |
| `provider` | TEXT | `cloud` / `ollama` / `rule_only` |
| `model_name` | TEXT | 如 `deepseek-chat` |
| `params_json` | TEXT | temperature 等关键参数（**不含密钥**） |
| `prompt_chars` | INTEGER | 便于 UI 提示发送量 |
| `status` | TEXT | `ok` / `failed` / `timeout` / `format_error` |
| `error_type` | TEXT NULL | |
| `duration_ms` | INTEGER | |
| `retry_count` | INTEGER | |

### 10. `retrieval_record` 检索记录

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT PK | |
| `task_id` | TEXT FK | |
| `query` | TEXT | |
| `hit_kind` | TEXT | `rule` / `card` |
| `hit_id` | TEXT | 规则或卡片 ID |
| `score` | REAL | BM25 分或规则匹配分 |
| `source_ref` | TEXT NULL | 章节级引用；空则 UI 标 `[来源未确认]` |
| `applicable_chip` | TEXT | |

### 11. `report` 诊断报告

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT PK | |
| `task_id` | TEXT FK UNIQUE | |
| `md_content` | TEXT NOT NULL | Markdown 全文（保留全部证据标签） |
| `generated_at` | TEXT | |
| `version` | INTEGER | 重新生成时递增 |

### 12. `task_event` 状态变更日志

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT PK | |
| `task_id` | TEXT FK | |
| `from_status` / `to_status` | TEXT | |
| `reason` | TEXT | |
| `at` | TEXT | |

---

## 三、枚举定义

### 3.1 任务状态 `status`

`draft` / `analyzing` / `awaiting_verification` / `verifying` / `resolved` / `unresolved` / `reported` / `failed`

### 3.2 证据标签 `evidence_tag`（六级，与 AGENTS.md 4.1 一致）

| 值 | 含义 |
|---|---|
| `HW_TEST` | `[实测-硬件]` |
| `SIM_TEST` | `[实测-仿真]` |
| `STATIC` | `[静态分析]` |
| `INFER` | `[推断]` |
| `ASSUME` | `[假设]` |
| `TODO_VERIFY` | `[待验证]` |

附加（仅用于演示数据，不参与诊断推理）：`DEMO` = `[演示数据]`。

### 3.3 库类型 `lib_type`

`spl`（标准外设库）/ `hal`（HAL 库）/ `unknown`

### 3.4 现象分类 `symptom_category`

| 值 | 现象 |
|---|---|
| `S1` | 串口没有输出 |
| `S2` | 串口输出乱码 |
| `S3` | TX 引脚没有预期波形 |
| `S4` | 串口接收失败 |
| `S5` | 初始化正常但不发送 |
| `S6` | 串口中断/接收处理异常 |

---

## 四、设计约束

1. **大内容不入库**：材料正文与工具大输出落文件系统，表内只存路径与摘要，避免 SQLite 膨胀。
2. **全链路留痕**：`tool_invocation`、`model_call`、`retrieval_record`、`task_event` 四张表构成 trace，前端时间线直接消费。
3. **不允许物理删除**：任务删除为软删除（`deleted_at`），避免误删实测证据。
4. **无用户表**：单用户本地工具，不引入鉴权（`PROJECT_PLAN.md` 第六节：不默认引入权限体系）。
5. **证据标签非空约束**：`hypothesis.evidence_tag`、`evidence.evidence_tag` 数据库层面 NOT NULL，从存储层强制合规。

---

**Phase 1 交付物 ④ 结束。本文为数据设计草案，未创建任何数据库或代码。**
