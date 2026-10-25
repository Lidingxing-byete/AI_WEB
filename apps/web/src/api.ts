/**
 * 后端 API 客户端。
 *
 * 约定：所有请求走 `/api`，由 vite 代理到 127.0.0.1:8787。
 */

export const API = '/api'

export interface Task {
  id: string
  title: string
  chip: string
  symptom_text: string
  status: string
  overall_tag: string
  pclk2_hz: number | null
  pclk1_hz: number | null
  actual_hse_hz: number | null
  /** 用户实测波特率（反向比对用）。由用户填写，服务端不代填。 */
  observed_baud: number | null
  observed_baud_source: string
  created_at: number
  updated_at: number
}

/** 一次工具调用的审计记录（比 trace 多出入参、耗时与失败原因）。 */
export interface ToolRun {
  seq: number
  tool_name: string
  status: string
  reason: string
  input_sum: string
  output_sum: string
  duration_ms: number
  error: string | null
}

export interface ToolSpec {
  name: string
  description: string
  default_tag: string
}

/** 硬件实测记录（Phase 5）。只由用户提交，系统不生成。 */
export interface HardwareEvidence {
  id: string
  case_id: string
  kind: string
  preset: string
  observed_baud: number | null
  verdict: string
  verdict_detail: string
  evidence_tag: string
  recorded_by: string
  note: string
  raw_bytes: number
  created_at: number
}

/** 实测现象与案例预期的一致性比对结论。 */
export interface PhenomMatch {
  case_id: string
  rule_id: string
  cause: string
  expected_tx: string
  actual_tx: string
  consistent: boolean
  evidence_tag: string
  note: string
  discriminator: string
}

/** 模型层状态（本地 Ollama；云端未启用时为空）。 */
export interface LlmStatus {
  enabled: boolean
  provider: string
  host: string
  model: string
  available: boolean
  reason: string
  models: string[]
  cloud_provider: string
  note: string
}

/**
 * 模型解读结果。
 *
 * 关键：模型只解释已查出的结论、补充验证建议，**不参与根因判定与评分**；
 * 输出恒为 `[推断]`，不可用时 degraded=true 且给出原因。
 */
export interface LlmNarrative {
  available: boolean
  degraded: boolean
  reason: string
  provider: string
  model: string
  duration_ms: number
  summary: string
  extra_steps: string[]
  cautions: string[]
  tag: string
  call?: {
    model?: string
    ok?: boolean
    duration_ms?: number
    error?: string
    prompt_chars?: number
    response_chars?: number
    eval_count?: number
    temperature?: number
  }
}

export interface Followup {
  seq: number
  item: string
}

export interface Evidence {
  kind: string
  item: string
  detail: string
  evidence_tag: string
}

/** 内置演示样例。全部标 [演示数据]，与真实材料在 UI 上区分（P1-D7）。 */
export interface Sample {
  id: string
  demo: boolean
  title: string
  symptom: string
  note: string
  /** 所属案例，如 C1 */
  case: string
  /** fault = 故障注入版；fixed = 对照组（用于验证不误报） */
  variant: string
  /** 预期命中的规则号。用于"预期 vs 实际"对照，避免演示时靠嘴说。 */
  expect_rules: string[]
  size_bytes: number
}

export interface Verification {
  id: string
  seq: number
  action: string
  expectation: string
  evidence_tag: string
  result_count?: number
  hypothesis_title?: string
  /** 已回填的实测观测值（未回填时为空） */
  observed?: string | null
  /** match | mismatch | partial | unknown */
  match?: string | null
  recorded_by?: string | null
  recorded_at?: number | null
}

export interface Hypothesis {
  id: string
  task_id: string
  rule_id: string
  title: string
  score: number
  evidence_tag: string
  status: string
  rank: number
  evidences: Evidence[]
  verifications: Verification[]
}

export interface TraceStep {
  seq: number
  tool_name: string
  status: string
  reason: string
  summary: string
}

export interface UnknownItem {
  item: string
  need: string
}

export interface Reference {
  card_id: string
  title: string
  score: number
  source_ref: string
  matched_terms: string
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(API + path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try {
      const j = await res.json()
      msg = j.detail ?? msg
    } catch {
      /* 忽略解析失败，用状态码即可 */
    }
    throw new Error(msg)
  }
  return (await res.json()) as T
}

export const api = {
  health: () => req<{ ok: boolean; corpus: { rules: number; cards: number } }>('/health'),

  createTask: (body: Partial<Task>) =>
    req<Task>('/tasks', { method: 'POST', body: JSON.stringify(body) }),

  listTasks: () => req<{ items: Task[] }>('/tasks'),

  getTask: (id: string) => req<Task>(`/tasks/${id}`),

  addArtifact: (id: string, kind: string, name: string, content: string) =>
    req<{ id: string }>(`/tasks/${id}/artifacts`, {
      method: 'POST',
      body: JSON.stringify({ kind, name, content }),
    }),

  listArtifacts: (id: string) =>
    req<{ items: { id: string; kind: string; name: string; size_bytes: number }[] }>(
      `/tasks/${id}/artifacts`,
    ),

  getArtifact: (id: string, aid: string) =>
    req<{ content: string }>(`/tasks/${id}/artifacts/${aid}`),

  getTrace: (id: string) => req<{ items: TraceStep[] }>(`/tasks/${id}/trace`),

  /** 工具调用全量留痕（含耗时、入参摘要、失败原因）。 */
  getToolRuns: (id: string) =>
    req<{
      items: ToolRun[]
      stats: { total: number; called: number; skipped: number; error: number; total_ms: number }
    }>(`/tasks/${id}/tool-runs`),

  /** 本轮诊断还需要补充什么（多步诊断入口）。 */
  getFollowups: (id: string) => req<{ items: Followup[] }>(`/tasks/${id}/followups`),

  listTools: () => req<{ items: ToolSpec[] }>('/tools'),

  /** 模型层在线状态。用于显示"模型真的在跑"，而不是假装在跑。 */
  getLlmStatus: () => req<LlmStatus>('/llm/status'),

  /** 本任务的模型调用记录（元数据，不含提示词原文）。 */
  getLlmCalls: (id: string) => req<{ items: (LlmNarrative & { created_at: number })[] }>(
    `/tasks/${id}/llm-calls`,
  ),

  /** 内置演示样例（分组信息由后端给出，前端不硬编码）。 */
  listSamples: () => req<{ items: Sample[] }>('/samples'),

  getSample: (sid: string) => req<Sample & { content: string }>(`/samples/${sid}`),

  listHwEvidence: (id: string) =>
    req<{ items: HardwareEvidence[] }>(`/tasks/${id}/hw-evidence`),

  addHwEvidence: (
    id: string,
    body: {
      kind: string
      raw_text: string
      recorded_by: string
      case_id?: string
      preset?: string
      observed_baud?: number
      evidence_tag?: string
      note?: string
    },
  ) =>
    req<{
      id: string
      evidence_tag: string
      verdict: string
      verdict_detail: string
      bytes: number
    }>(`/tasks/${id}/hw-evidence`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  deleteHwEvidence: (id: string, hid: string) =>
    req<{ deleted: string }>(`/tasks/${id}/hw-evidence/${hid}`, { method: 'DELETE' }),

  patchTask: (id: string, body: Partial<Task>) =>
    req<Task>(`/tasks/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),

  /**
   * 补充材料后重新诊断（同步）。
   * 走的是与 SSE 完全相同的引擎路径，不存在两套诊断逻辑。
   */
  reassess: (id: string, useLlm = false) =>
    req<{
      status: string
      hypotheses: any[]
      unknowns: any[]
      followups: string[]
      tool_runs: any[]
      llm_narrative: LlmNarrative | null
    }>(`/tasks/${id}/reassess?use_llm=${useLlm ? 1 : 0}`, { method: 'POST' }),

  getHypotheses: (id: string) => req<{ items: Hypothesis[] }>(`/tasks/${id}/hypotheses`),

  getUnknowns: (id: string) => req<{ items: UnknownItem[] }>(`/tasks/${id}/unknowns`),

  getReferences: (id: string) => req<{ items: Reference[] }>(`/tasks/${id}/references`),

  getVerifications: (id: string) => req<{ items: Verification[] }>(`/tasks/${id}/verifications`),

  /** 回填实测结果。observed / match 缺失时后端返回 422。 */
  submitResult: (
    id: string,
    vid: string,
    body: { observed: string; match: string; recorded_by: string; raw_ref?: string },
  ) =>
    req<{ test_result_id: string; evidence_tag: string }>(
      `/tasks/${id}/verifications/${vid}/result`,
      { method: 'POST', body: JSON.stringify(body) },
    ),

  /** 拉取诊断报告。regenerate=true 时后端重新生成并写入新版本；默认读取最新已存版本。 */
  getReport: (id: string, regenerate = false) =>
    req<{ md_content: string; version: number }>(
      `/tasks/${id}/report?regenerate=${regenerate ? 1 : 0}`,
    ),
}

/**
 * SSE 事件流。
 *
 * 这里消费的是**后端真实 trace**（引擎每完成一步即推送），
 * 不是前端伪造的动画。
 */
export function openDiagnoseStream(
  taskId: string,
  handlers: {
    onEvent: (type: string, data: any) => void
    onClose?: () => void
  },
  useLlm = false,
): () => void {
  // use_llm 默认 0：默认诊断路径是纯规则层，不依赖模型可用性
  const es = new EventSource(
    `${API}/tasks/${taskId}/diagnose?use_llm=${useLlm ? 1 : 0}`,
  )
  const types = [
    'step_start',
    'symptom',
    'step_done',
    'hypothesis_update',
    'retrieval_done',
    'followups',
    'phenomenon',
    'phenomenon_matches',
    'llm_narrative',
    'done',
    'error',
    'closed',
  ]
  for (const t of types) {
    es.addEventListener(t, (e: MessageEvent) => {
      try {
        handlers.onEvent(t, JSON.parse(e.data))
      } catch {
        handlers.onEvent(t, {})
      }
    })
  }
  es.onerror = () => {
    es.close()
    handlers.onClose?.()
  }
  return () => es.close()
}
