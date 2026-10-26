import { create } from 'zustand'
import {
  api,
  openDiagnoseStream,
  type CloudProviderInfo,
  type VerifyKeyResult,
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
  /** 正在切换任务 / 拉取该任务的全量结果。用于显示"载入中"，避免空白误读成"没结果" */
  taskLoading: boolean
  /** 当前任务若来自内置样例，记录样例 id，用于"预期 vs 实际"对照 */
  demoSampleId: string | null

  /** 模型层：状态、本轮解读、开关。模型不可用时系统照常工作（降级）。 */
  llmStatus: LlmStatus | null
  llmNarrative: LlmNarrative | null
  useLlm: boolean
  /** 模型来源偏好：auto（云端就绪优先）/ local / cloud（失败自动降级） */
  llmPreferred: 'auto' | 'local' | 'cloud'

  /**
   * 测试者自己的云端密钥：**只存 sessionStorage**（关标签页即消失），
   * 随请求头发给后端，后端用完即丢、不落盘。
   */
  testerKey: string
  testerModel: string
  /** 测试者选择的云端服务商（服务端白名单里的 id） */
  testerProvider: string
  /** 服务端可选服务商目录（只读） */
  providers: CloudProviderInfo[]
  /** 最近一次密钥校验结果（ok/masked/reason） */
  keyCheck: VerifyKeyResult | null
  /** 保存（本标签页）并校验；返回 {ok, message} 供界面提示 */
  applyTesterKey: (key: string, provider?: string,
                   model?: string) => Promise<{ ok: boolean; message: string }>
  /** 拉取可选服务商（失败时保持空列表，不阻塞主流程） */
  loadProviders: () => Promise<void>
  setTesterProvider: (id: string) => void
  /** 删除本标签页里的密钥（测试结束必须点它） */
  clearTesterKey: () => void

  /** 实测记录人：写进每条实测证据，避免固定写死某个人名（可改，存 localStorage） */
  recordedBy: string
  setRecordedBy: (name: string) => void
  /** 删除（软删除）任务：后端已有 DELETE 端点，前端提供入口 */
  deleteTask: (id: string) => Promise<void>

  error: string | null
  /** 清空错误提示（用户看过之后手动关闭） */
  clearError: () => void

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
  setLlmPreferred: (p: 'auto' | 'local' | 'cloud') => void
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

/**
 * 任务级状态：切换 / 新建任务时必须整体清空。
 * 否则上一轮的工具留痕、实测记录、现象比对会留在新任务界面上——
 * 看起来像"新任务已经诊断过"，属于会误导人的状态泄漏。
 */
const EMPTY_TASK_SCOPED = {
  artifacts: [] as State['artifacts'],
  events: [] as StreamEvent[],
  streaming: false,
  hypotheses: [] as Hypothesis[],
  unknowns: [] as UnknownItem[],
  references: [] as Reference[],
  verifications: [] as Verification[],
  trace: [] as TraceStep[],
  toolRuns: [] as ToolRun[],
  runStats: null as State['runStats'],
  followups: [] as Followup[],
  hwEvidence: [] as HardwareEvidence[],
  phenomenon: null as any,
  phenomMatches: [] as PhenomMatch[],
  llmNarrative: null as LlmNarrative | null,
  demoSampleId: null as string | null,
}

/** 统一错误文案：HTTP 错误与网络错误都能显示出来，避免 Promise 静默失败。 */
function errText(e: unknown): string {
  const m = (e as any)?.message
  return typeof m === 'string' && m ? m : String(e)
}

const RECORDER_KEY = 'xz_recorded_by'
// 注意：用 sessionStorage 而不是 localStorage —— 关闭标签页即清除，
// 测试者的密钥不会被长期留在这台机器的浏览器里。
const TESTER_KEY = 'xz_tester_cloud_key'
const TESTER_MODEL = 'xz_tester_cloud_model'
const TESTER_PROVIDER = 'xz_tester_cloud_provider'

/** 记录人默认值：不写死任何个人姓名，用户可改（存本机）。 */
function loadRecorder(): string {
  try {
    return localStorage.getItem(RECORDER_KEY) || '本机用户'
  } catch {
    return '本机用户'
  }
}

/** 读取本标签页里的测试密钥（sessionStorage，关页即失效）。 */
function loadTesterKey(): string {
  try {
    return sessionStorage.getItem(TESTER_KEY) || ''
  } catch {
    return ''
  }
}

function loadTesterModel(): string {
  try {
    return sessionStorage.getItem(TESTER_MODEL) || ''
  } catch {
    return ''
  }
}

function loadTesterProvider(): string {
  try {
    return sessionStorage.getItem(TESTER_PROVIDER) || 'deepseek'
  } catch {
    return 'deepseek'
  }
}

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
  taskLoading: false,
  demoSampleId: null,
  llmStatus: null,
  llmNarrative: null,
  // 默认开启模型解读：这是"AI 技术深度"的展示面。
  // 但后端 use_llm 默认 0，且模型不可用时自动降级——演示不会因此中断。
  useLlm: true,
  // 模型来源偏好（与后端 XZ_LLM_PREFERRED 对应，前端可运行时切换）：
  // auto=云端就绪优先否则本地；local=固定本地；cloud=固定云端（失败自动降级本地）
  llmPreferred: 'auto',
  testerKey: loadTesterKey(),
  testerModel: loadTesterModel(),
  testerProvider: loadTesterProvider(),
  providers: [],
  keyCheck: null,
  recordedBy: loadRecorder(),
  error: null,

  async setUseLlm(on: boolean) {
    set({ useLlm: on })
  },

  async setLlmPreferred(p: 'auto' | 'local' | 'cloud') {
    set({ llmPreferred: p })
  },

  async applyTesterKey(key: string, provider = 'deepseek', model = '') {
    const k = key.trim()
    if (k.length < 8) {
      return { ok: false, message: '密钥看起来不完整（至少 8 位）' }
    }
    try {
      const r = await api.verifyCloudKey(k, provider, model.trim())
      // 无论校验是否通过都先记到本标签页：失败也要能看到"是哪把钥匙失败了"
      set({
        testerKey: k,
        testerModel: model.trim(),
        testerProvider: provider,
        keyCheck: r,
      })
      try {
        sessionStorage.setItem(TESTER_KEY, k)
        sessionStorage.setItem(TESTER_MODEL, model.trim())
        sessionStorage.setItem(TESTER_PROVIDER, provider)
      } catch {
        /* 隐私模式：只在内存里用，仍可工作 */
      }
      await get().loadLlmStatus()
      return {
        ok: r.ok,
        message: r.ok
          ? `密钥可用（${r.provider_label} · ${r.model} · ${r.masked}）：仅保存在本标签页，测试结束请点「删除密钥」`
          : `校验未通过：${r.reason}。请检查 Key、服务商是否选对、账户是否有额度`,
      }
    } catch (e: unknown) {
      return { ok: false, message: `校验请求失败：${errText(e)}` }
    }
  },

  async loadProviders() {
    try {
      const r = await api.getCloudProviders()
      set({ providers: r.items })
    } catch {
      set({ providers: [] })
    }
  },

  setTesterProvider(id: string) {
    set({ testerProvider: id })
    try {
      sessionStorage.setItem(TESTER_PROVIDER, id)
    } catch {
      /* 忽略 */
    }
  },

  clearTesterKey() {
    set({ testerKey: '', testerModel: '', keyCheck: null })
    try {
      sessionStorage.removeItem(TESTER_KEY)
      sessionStorage.removeItem(TESTER_MODEL)
    } catch {
      /* 忽略 */
    }
    void get().loadLlmStatus()
  },

  setRecordedBy(name: string) {
    const v = name.trim().slice(0, 40) || '本机用户'
    set({ recordedBy: v })
    try {
      localStorage.setItem(RECORDER_KEY, v)
    } catch {
      /* 隐私模式 / 禁用存储：只影响下次默认值，不影响本次记录 */
    }
  },

  clearError() {
    set({ error: null })
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
      // 带上本标签页的密钥：后端据此显示"我的密钥是否可用"（不保存）
      const s = await api.getLlmStatus(
        get().testerKey, get().testerModel, get().testerProvider)
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
    // 切换任务先掐断上一个任务的 SSE：否则旧流的事件会继续写进新任务，
    // 结束回调还会替新任务刷新一次，界面出现"串味"的数据。
    closeStream?.()
    closeStream = null
    set({ taskLoading: true })
    try {
      const task = await api.getTask(id)
      const arts = await api.listArtifacts(id)
      set({ ...EMPTY_TASK_SCOPED, task, artifacts: arts.items, error: null })
      await get().refreshResults()
    } catch (e: unknown) {
      set({ error: `打开任务失败：${errText(e)}` })
      throw e
    } finally {
      set({ taskLoading: false })
    }
  },

  async newTask(title: string, symptom: string) {
    closeStream?.()
    closeStream = null
    try {
      const t = await api.createTask({
        title: title.trim() || '未命名诊断任务',
        symptom_text: symptom,
        pclk2_hz: 72000000,
      })
      set({ ...EMPTY_TASK_SCOPED, task: t, error: null })
      await get().loadTasks()
      return t.id
    } catch (e: unknown) {
      set({ error: `新建任务失败：${errText(e)}` })
      throw e
    }
  },

  async addSource(content: string, name = 'main.c') {
    const id = get().task?.id
    if (!id) return
    if (!content.trim()) {
      set({ error: '材料内容为空，未提交' })
      return
    }
    try {
      await api.addArtifact(id, 'source_code', name, content)
      const arts = await api.listArtifacts(id)
      set({ artifacts: arts.items, error: null })
    } catch (e: unknown) {
      set({ error: `提交材料失败：${errText(e)}` })
    }
  },

  async deleteTask(id: string) {
    // 删的正好是当前任务：先关流，避免诊断回写到一个已删除的任务
    if (get().task?.id === id) {
      closeStream?.()
      closeStream = null
    }
    try {
      await api.deleteTask(id)
      if (get().task?.id === id) set({ ...EMPTY_TASK_SCOPED, task: null })
      await get().loadTasks()
    } catch (e: unknown) {
      set({ error: `删除任务失败：${errText(e)}` })
    }
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
      // 上一轮的工具留痕/现象/待补充项必须清掉：否则诊断进行中会显示
      // 上一轮的"工具 5/8"，看起来像本轮已经跑过。
      toolRuns: [],
      runStats: null,
      followups: [],
      phenomenon: null,
      phenomMatches: [],
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
        // 时序要点：引擎的 done 事件在 _persist_report 落库**之前**发出
        // （后端 worker：run → 发 done → 落库 → closed）。若收到 done 就
        // 拉全量，会与落库竞态，偶发拉到空数据（界面显示"漏检/工具0/0"
        // 而 DB 实际完整）。因此 done 只结束流式阶段；落库完成由
        // closed 事件确认后再 refresh。
        if (type === 'done') {
          set({ streaming: false })
        }
        if (type === 'closed') {
          finish()
        }
        if (type === 'error') {
          set({ error: data.message ?? '诊断出错' })
          finish()
        }
      },
      onClose: () => finish(),
    }, get().useLlm, get().llmPreferred, get().testerKey, get().testerModel,
       get().testerProvider)
    })
  },

  async refreshResults() {
    const id = get().task?.id
    if (!id) return
    try {
      const [t, hy, un, re, ve, tr, runs, fu, hw, lc] = await Promise.all([
        api.getTask(id),
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
        // 同步任务状态：诊断完成后后端置 awaiting_verification，
        // 左侧"当前任务"不应停留在 created。
        task: t,
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
    if (!observed.trim()) {
      set({ error: '实测观测值为空：这条记录必须由你填写，系统不会代填' })
      return
    }
    try {
      await api.submitResult(id, vid, {
        observed,
        match,
        recorded_by: get().recordedBy,
      })
      await api.reassess(id, get().useLlm, get().llmPreferred, get().testerKey,
        get().testerModel, get().testerProvider)
      await get().refreshResults()
      set({ error: null })
    } catch (e: unknown) {
      set({ error: `回填失败：${errText(e)}` })
    }
  },

  /**
   * 一键载入内置样例并跑完诊断（演示路径）。
   *
   * 全程标 [演示数据]：任务名带前缀，样例源码是人为注入的故障，
   * 不代表任何真实板卡现象。
   *
   * 诊断在后台流式执行（SSE），不阻塞调用方：这样"点另一个样例"
   * 在诊断进行中也允许打断切换（runDiagnose 会关闭旧流再开新流）。
   */
  async loadDemo(sampleId: string) {
    try {
      const s = await api.getSample(sampleId)
      const stillWanted = () => get().demoSampleId === sampleId
      await get().newTask(`[演示数据] ${s.title}`, s.symptom)
      // newTask 会清空任务级状态（含 demoSampleId），这里在它之后打标记
      set({ demoSampleId: sampleId })
      await get().addSource(s.content, `${sampleId}.c`)
      if (!stillWanted()) return
      void get().runDiagnose()
    } catch (e: unknown) {
      set({ error: `载入示例失败：${errText(e)}` })
    }
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
    if (!Number.isFinite(baud) || baud <= 0) {
      set({ error: '实测波特率必须是正数（单位 bps）' })
      return
    }
    try {
      await api.patchTask(id, { observed_baud: baud, observed_baud_source: source })
      const t = await api.getTask(id)
      set({ task: t })
      await api.reassess(id, get().useLlm, get().llmPreferred, get().testerKey,
        get().testerModel, get().testerProvider)
      await get().refreshResults()
      set({ error: null })
    } catch (e: unknown) {
      set({ error: `提交实测波特率失败：${errText(e)}` })
    }
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
    if (!id || !raw.trim()) {
      if (id) set({ error: '抓帧内容为空：请粘贴串口助手真实采集到的原始数据' })
      return
    }
    try {
      await api.addHwEvidence(id, {
        kind: 'serial_capture',
        raw_text: raw,
        recorded_by: get().recordedBy,
        case_id: meta.case_id ?? '',
        preset: meta.preset ?? '',
        note: meta.note ?? '',
        observed_baud: meta.observed_baud,
      })
      await api.reassess(id, get().useLlm, get().llmPreferred, get().testerKey,
        get().testerModel, get().testerProvider)
      await get().refreshResults()
      set({ error: null })
    } catch (e: unknown) {
      set({ error: `提交抓帧失败：${errText(e)}` })
    }
  },

  async removeHwEvidence(hid: string) {
    const id = get().task?.id
    if (!id) return
    try {
      await api.deleteHwEvidence(id, hid)
      await get().refreshResults()
      set({ error: null })
    } catch (e: unknown) {
      set({ error: `删除实测记录失败：${errText(e)}` })
    }
  },
}))
