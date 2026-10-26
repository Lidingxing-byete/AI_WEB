/**
 * 后端 API 客户端。
 *
 * 约定：请求走 `<BASE>/api`。
 *  - 本地开发：BASE='/', 由 vite 代理到 127.0.0.1:8787；
 *  - 部署到子路径（如 https://域名/xz/）：BASE='/xz/'（构建时 VITE_BASE 指定），
 *    由 Nginx 把 /xz/api 反代到后端，前后端同源，不需要 CORS。
 */
const BASE = (import.meta.env.BASE_URL || '/').replace(/\/+$/, '')

export const API = `${BASE}/api`

/**
 * 测试者自带密钥的请求头。
 *
 * 只在此处构造、只随请求发出：不写 localStorage（只放 sessionStorage）、
 * 不进 URL（URL 会进服务端访问日志，等于把密钥写进日志）、不进界面文本。
 */
function keyHeaders(cloudKey: string, model = '', provider = ''): Record<string, string> {
  const h: Record<string, string> = {}
  if (cloudKey.trim()) h['X-Cloud-Key'] = cloudKey.trim()
  if (model.trim()) h['X-Cloud-Model'] = model.trim()
  if (provider.trim()) h['X-Cloud-Provider'] = provider.trim()
  return h
}

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

/** 服务商目录条目（服务端白名单，前端只读）。 */
export interface CloudProviderInfo {
  id: string
  label: string
  base_url: string
  default_model: string
  models: string[]
  note: string
}

/** 密钥校验结果。**永远只有掩码**，明文只在请求头里传一次。 */
export interface VerifyKeyResult {
  ok: boolean
  masked: string
  provider: string
  provider_label: string
  model: string
  reason: string
  /** 固定为 none：服务端不保存任何密钥 */
  storage: string
}

/** 模型层状态（本地 Ollama；云端未启用时为空）。 */
export interface CloudStatus {
  provider: string
  base_url: string
  model: string
  configured: boolean
  available: boolean
  reason: string
  /** session=测试者本次带入；env=服务端环境变量；none=未配置 */
  key_source?: 'session' | 'env' | 'none'
  key_masked?: string
  updated_at?: number | null
}

export interface LlmStatus {
  enabled: boolean
  provider: string
  host: string
  model: string
  available: boolean
  reason: string
  models: string[]
  preferred: string
  active_hint: string
  cloud_provider: string
  cloud_provider_label?: string
  cloud: CloudStatus
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
    ...init,
    // 兼容调用方追加自定义头（如 X-Admin-Token），而不是被整体覆盖
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
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

  /** 软删除任务：后端置 deleted=1，列表不再返回；实测记录保留在库里。 */
  deleteTask: (id: string) =>
    req<{ id: string; deleted: boolean }>(`/tasks/${id}`, { method: 'DELETE' }),

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

  /**
   * 模型层在线状态。
   *
   * 传 `cloudKey` 时后端按**你的密钥**探测云端（不保存、只用这一次），
   * 用于面板显示"我的密钥是否可用"。
   */
  getLlmStatus: (cloudKey = '', model = '', provider = '') =>
    req<LlmStatus>('/llm/status', { headers: keyHeaders(cloudKey, model, provider) }),

  /** 可选云端服务商（白名单）。新增服务商只改后端预设表，前端下拉自动出现。 */
  getCloudProviders: () =>
    req<{ items: CloudProviderInfo[]; default: string }>('/llm/providers'),

  /**
   * 校验测试者自己的云端密钥：服务端只做一次 `GET /models` 鉴权探测
   * （不触发计费推理、不落盘），返回 ok 与掩码。
   */
  verifyCloudKey: (apiKey: string, provider = 'deepseek', model = '') =>
    req<VerifyKeyResult>('/llm/verify-key', {
      method: 'POST',
      body: JSON.stringify({ api_key: apiKey, provider, model }),
    }),

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
  reassess: (id: string, useLlm = false, provider = 'auto',
             cloudKey = '', model = '', cloudProvider = '') =>
    req<{
      status: string
      hypotheses: any[]
      unknowns: any[]
      followups: string[]
      tool_runs: any[]
      llm_narrative: LlmNarrative | null
    }>(
      `/tasks/${id}/reassess?use_llm=${useLlm ? 1 : 0}&provider=${encodeURIComponent(provider)}`,
      { method: 'POST', headers: keyHeaders(cloudKey, model, cloudProvider) },
    ),

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
 * SSE 事件流（fetch + ReadableStream 实现）。
 *
 * 为什么不用 EventSource：测试者自带的密钥必须放在**请求头**里。
 * EventSource 不支持自定义头，只能把密钥塞进 URL——而 URL 会进
 * Nginx/uvicorn 访问日志，等于把密钥写进日志。因此这里用 fetch 读流、
 * 自己解析 SSE 帧，密钥只走 header。
 *
 * 这里消费的是**后端真实 trace**（引擎每完成一步即推送），不是前端伪造的动画。
 */
export function openDiagnoseStream(
  taskId: string,
  handlers: {
    onEvent: (type: string, data: any) => void
    onClose?: () => void
  },
  useLlm = false,
  provider = 'auto',
  cloudKey = '',
  cloudModel = '',
  cloudProvider = '',
): () => void {
  const ctrl = new AbortController()
  const url = `${API}/tasks/${taskId}/diagnose?use_llm=${useLlm ? 1 : 0}&provider=${encodeURIComponent(provider)}`

  void (async () => {
    try {
      const res = await fetch(url, {
        headers: {
          Accept: 'text/event-stream',
          ...keyHeaders(cloudKey, cloudModel, cloudProvider),
        },
        signal: ctrl.signal,
      })
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''

      // 逐帧解析：帧以空行分隔，`event:` 与 `data:` 各占一行
      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        let idx = buf.indexOf('\n\n')
        while (idx >= 0) {
          const frame = buf.slice(0, idx)
          buf = buf.slice(idx + 2)
          let evt = ''
          let data = ''
          for (const line of frame.split('\n')) {
            if (line.startsWith('event:')) evt = line.slice(6).trim()
            else if (line.startsWith('data:')) data += line.slice(5).trim()
          }
          if (evt) {
            try {
              handlers.onEvent(evt, data ? JSON.parse(data) : {})
            } catch {
              // 解析失败不能让整条流崩掉：留痕后按空对象继续
              console.warn(`[SSE] ${evt} 事件解析失败`, data)
              handlers.onEvent(evt, {})
            }
          }
          idx = buf.indexOf('\n\n')
        }
      }
    } catch (e) {
      // 主动 abort（切换任务 / 重新诊断）不算错误
      if (!ctrl.signal.aborted) console.warn('[SSE] 连接中断', e)
    } finally {
      handlers.onClose?.()
    }
  })()

  return () => ctrl.abort()
}
