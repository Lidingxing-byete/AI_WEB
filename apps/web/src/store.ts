import { create } from 'zustand'
import {
  api,
  openDiagnoseStream,
  type Followup,
  type HardwareEvidence,
  type Hypothesis,
  type LlmNarrative,
  type LlmStatus,
  type PhenomMatch,
  type Reference,
  type Sample,
  type Task,
  type ToolRun,
  type ToolSpec,
  type TraceStep,
  type UnknownItem,
  type Verification,
} from './api'

export interface StreamEvent {
  id: number
  type: string
  data: any
  at: number
}

interface State {
  tasks: Task[]
  task: Task | null
  artifacts: { id: string; kind: string; name: string; size_bytes: number }[]

  streaming: boolean
  events: StreamEvent[]
  hypotheses: Hypothesis[]
  unknowns: UnknownItem[]
  references: Reference[]
  verifications: Verification[]
  trace: TraceStep[]
  /** Phase 4：工具调用审计记录与"还需补充什么" */
  toolRuns: ToolRun[]
  runStats: { total: number; called: number; skipped: number; error: number; total_ms: number } | null
  followups: Followup[]
  /** Phase 5：硬件实测记录、实测现象判定、现象-预期比对 */
  hwEvidence: HardwareEvidence[]
  phenomenon: any | null
  phenomMatches: PhenomMatch[]

  /** Phase 7：系统能力清单与内置样例（用于演示与可解释性） */
  tools: ToolSpec[]
  samples: Sample[]
  /** 后端连通状态。断线时不显示"诊断完成"这类误导信息。 */
  backendUp: boolean | null
  /** 当前任务若来自内置样例，记录样例 id，用于"预期 vs 实际"对照 */
  demoSampleId: string | null

  /** 模型层：状态、本轮解读、开关。模型不可用时系统照常工作（降级）。 */
  llmStatus: LlmStatus | null
  llmNarrative: LlmNarrative | null
  useLlm: boolean

  error: string | null

  loadTasks: () => Promise<void>
  selectTask: (id: string) => Promise<void>
  newTask: (title: string, symptom: string) => Promise<string>
  addSource: (content: string, name?: string) => Promise<void>
  runDiagnose: () => Promise<void>
  refreshResults: () => Promise<void>
  submitResult: (vid: string, observed: string, match: string) => Promise<void>
  /** 一键载入内置样例并直接跑完诊断（演示路径，全程标 [演示数据]） */
  loadDemo: (sampleId: string) => Promise<void>
  checkHealth: () => Promise<void>
  loadTools: () => Promise<void>
  loadSamples: () => Promise<void>
  /** 探测本地模型是否可用（只读，不加载模型、不下载模型） */
  loadLlmStatus: () => Promise<void>
  setUseLlm: (on: boolean) => void
  /** 补充实测波特率后重新诊断（多步诊断的第二步） */
  submitObservedBaud: (baud: number, source: string) => Promise<void>
  /** 提交硬件实测抓帧（Phase 5）。raw 必须由用户粘贴，服务端拒绝空值。 */
  submitSerialCapture: (
    raw: string,
    meta: { case_id?: string; preset?: string; note?: string; observed_baud?: number },
  ) => Promise<void>
  removeHwEvidence: (hid: string) => Promise<void>
}

let closeStream: (() => void) | null = null
let eventSeq = 0

export const useStore = create<State>((set, get) => ({
  tasks: [],
  task: null,
  artifacts: [],
  streaming: false,
  events: [],
  hypotheses: [],
  unknowns: [],
  references: [],
  verifications: [],
  trace: [],
  toolRuns: [],
  runStats: null,
  followups: [],
  hwEvidence: [],
  phenomenon: null,
  phenomMatches: [],
  tools: [],
  samples: [],
  backendUp: null,
  demoSampleId: null,
  llmStatus: null,
  llmNarrative: null,
  // 默认开启模型解读：这是"AI 技术深度"的展示面。
  // 但后端 use_llm 默认 0，且模型不可用时自动降级——演示不会因此中断。
  useLlm: true,
  error: null,

  async setUseLlm(on: boolean) {
    set({ useLlm: on })
  },

  async checkHealth() {
    try {
      await api.health()
      set({ backendUp: true })
    } catch {
      set({ backendUp: false })
    }
  },

  async loadTools() {
    try {
      const r = await api.listTools()
      set({ tools: r.items })
    } catch {
      /* 工具清单只是可解释性展示，失败不影响诊断 */
    }
  },

  async loadSamples() {
    try {
      const r = await api.listSamples()
      set({ samples: r.items })
    } catch {
      set({ samples: [] })
    }
  },

  async loadLlmStatus() {
    try {
      const s = await api.getLlmStatus()
      set({ llmStatus: s })
    } catch {
      // 探测失败按"离线"处理：系统仍可正常诊断（纯规则层）
      set({ llmStatus: null })
    }
  },

  async loadTasks() {
    try {
      const r = await api.listTasks()
      set({ tasks: r.items })
    } catch (e: any) {
      set({ error: String(e.message ?? e) })
    }
  },

  async selectTask(id: string) {
    const task = await api.getTask(id)
    const arts = await api.listArtifacts(id)
    set({ task, artifacts: arts.items, events: [], error: null })
    await get().refreshResults()
  },

  async newTask(title: string, symptom: string) {
    const t = await api.createTask({ title, symptom_text: symptom, pclk2_hz: 72000000 })
    set({ task: t, artifacts: [], events: [], hypotheses: [], unknowns: [], references: [], verifications: [], trace: [] })
    await get().loadTasks()
    return t.id
  },

  async addSource(content: string, name = 'main.c') {
    const id = get().task?.id
    if (!id) return
    await api.addArtifact(id, 'source_code', name, content)
    const arts = await api.listArtifacts(id)
    set({ artifacts: arts.items })
  },

  /**
   * 跑一轮诊断。返回 Promise，在流结束（done/closed/error）后 resolve。
   *
   * 事件全部是后端真实推送的引擎执行轨迹，前端不伪造任何步骤。
   */
  async runDiagnose() {
    const id = get().task?.id
    if (!id) return
    closeStream?.()
    set({
      streaming: true,
      events: [],
      hypotheses: [],
      unknowns: [],
      references: [],
      error: null,
      llmNarrative: null,
    })

    return new Promise<void>((resolve) => {
      let settled = false
      const finish = () => {
        if (settled) return
        settled = true
        set({ streaming: false })
        void get().refreshResults().finally(() => resolve())
      }

      closeStream = openDiagnoseStream(id, {
      onEvent: (type, data) => {
        set((s) => ({ events: [...s.events, { id: ++eventSeq, type, data, at: Date.now() }] }))
        if (type === 'hypothesis_update') {
          // 流过程中的候选根因直接来自后端，落库后由 refreshResults 补齐证据
          set({ hypotheses: data.hypotheses ?? [] })
        }
        if (type === 'retrieval_done') {
          set({ references: data.hits ?? [] })
        }
        if (type === 'phenomenon') {
          // 实测现象由后端工具判定，前端只做呈现，不自行判读
          set({ phenomenon: data })
        }
        if (type === 'phenomenon_matches') {
          set({ phenomMatches: data.items ?? [] })
        }
        if (type === 'llm_narrative') {
          // 模型解读：恒为 [推断]，不参与根因判定。降级时 reason 非空。
          set({ llmNarrative: data })
        }
        if (type === 'followups') {
          set({
            followups: (data.items ?? []).map((s: string, i: number) => ({
              seq: i + 1,
              item: s,
            })),
          })
        }
        if (type === 'closed' || type === 'done') {
          finish()
        }
        if (type === 'error') {
          set({ error: data.message ?? '诊断出错' })
          finish()
        }
      },
      onClose: () => finish(),
    }, get().useLlm)
    })
  },

  async refreshResults() {
    const id = get().task?.id
    if (!id) return
    try {
      const [hy, un, re, ve, tr, runs, fu, hw, lc] = await Promise.all([
        api.getHypotheses(id),
        api.getUnknowns(id),
        api.getReferences(id),
        api.getVerifications(id),
        api.getTrace(id),
        api.getToolRuns(id),
        api.getFollowups(id),
        api.listHwEvidence(id),
        api.getLlmCalls(id),
      ])
      set({
        hypotheses: hy.items,
        unknowns: un.items,
        references: re.items,
        verifications: ve.items,
        trace: tr.items,
        toolRuns: runs.items,
        runStats: runs.stats ?? null,
        followups: fu.items,
        hwEvidence: hw.items,
        // 未启用模型时库里没有记录 → 置空，绝不显示上一轮的旧解读
        llmNarrative: lc.items[0] ?? null,
      })
    } catch {
      /* 尚未诊断时部分表为空，忽略 */
    }
  },

  /**
   * 回填实测结果，并据此重排候选。
   *
   * 用户排除一条，它就该往下掉——否则等于系统"听见了但没反应"。
   * 重跑由后端保证不丢实测记录（_replay_test_results），这里才敢直接重排。
   */
  async submitResult(vid: string, observed: string, match: string) {
    const id = get().task?.id
    if (!id) return
    await api.submitResult(id, vid, {
      observed,
      match,
      recorded_by: 'Zero',
    })
    await api.reassess(id, get().useLlm)
    await get().refreshResults()
  },

  /**
   * 一键载入内置样例并跑完诊断（演示路径）。
   *
   * 全程标 [演示数据]：任务名带前缀，样例源码是人为注入的故障，
   * 不代表任何真实板卡现象。
   */
  async loadDemo(sampleId: string) {
    const s = await api.getSample(sampleId)
    const tid = await get().newTask(`[演示数据] ${s.title}`, s.symptom)
    await get().addSource(s.content, `${sampleId}.c`)
    set({ demoSampleId: sampleId })
    await get().runDiagnose()
  },

  /**
   * 用户补充实测波特率 -> 落库 -> 触发重评估。
   *
   * 这是"多步诊断"的关键一步：第一轮因缺少实测值只能给出 unknown，
   * 用户补齐后系统重新计算并更新候选根因，而不是一次性猜到底。
   */
  async submitObservedBaud(baud: number, source: string) {
    const id = get().task?.id
    if (!id) return
    await api.patchTask(id, { observed_baud: baud, observed_baud_source: source })
    const t = await api.getTask(id)
    set({ task: t })
    await api.reassess(id, get().useLlm)
    await get().refreshResults()
  },

  /**
   * 提交用户上板采集的串口抓帧（Phase 5 硬件闭环第一步）。
   *
   * 纪律：raw 文本只能是用户粘贴的真实抓帧；提交后**不自动下结论**，
   * 而是重新跑一遍诊断，让引擎把实测现象与代码检查结果做比对
   *（比对结论标 [推断]，实测现象本身标 [实测-硬件]）。
   */
  async submitSerialCapture(raw, meta) {
    const id = get().task?.id
    if (!id || !raw.trim()) return
    await api.addHwEvidence(id, {
      kind: 'serial_capture',
      raw_text: raw,
      recorded_by: 'Zero',
      case_id: meta.case_id ?? '',
      preset: meta.preset ?? '',
      note: meta.note ?? '',
      observed_baud: meta.observed_baud,
    })
    await api.reassess(id, get().useLlm)
    await get().refreshResults()
  },

  async removeHwEvidence(hid: string) {
    const id = get().task?.id
    if (!id) return
    await api.deleteHwEvidence(id, hid)
    await get().refreshResults()
  },
}))
