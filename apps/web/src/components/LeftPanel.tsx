import { useEffect, useState } from 'react'
import { api, type Sample } from '../api'
import { useStore } from '../store'
import { Card } from './EvidenceTag'

/** 任务状态 -> 中文标签与样式（与后端取值一致：created / awaiting_verification / insufficient）。 */
const TASK_STATUS_LABEL: Record<string, string> = {
  created: '未诊断',
  awaiting_verification: '已诊断·待验证',
  insufficient: '材料不足',
}

const TASK_STATUS_STYLE: Record<string, string> = {
  created: 'bg-slate-100 text-slate-600',
  awaiting_verification: 'bg-emerald-50 text-emerald-700',
  insufficient: 'bg-amber-50 text-amber-700',
}

const TASK_STATUS_FILTERS: { value: string; label: string }[] = [
  { value: 'all', label: '全部状态' },
  { value: 'created', label: '未诊断' },
  { value: 'awaiting_verification', label: '已诊断·待验证' },
  { value: 'insufficient', label: '材料不足' },
]

/**
 * 左栏：任务与材料。
 *
 * 内置示例全部标 `[演示数据]`（P1-D7），与真实材料在 UI 上区分。
 * Phase 7 第二批：历史任务支持按标题/现象/ID 搜索 + 状态筛选。
 */
export function LeftPanel() {
  const { task, artifacts, addSource, newTask, runDiagnose, streaming, loadTasks, tasks,
    hwEvidence, submitSerialCapture, removeHwEvidence,
    samples, loadSamples, loadDemo, demoSampleId } = useStore()
  const [code, setCode] = useState('')
  const [capture, setCapture] = useState('')
  const [capCase, setCapCase] = useState('')
  const [title, setTitle] = useState('新建诊断任务')
  const [symptom, setSymptom] = useState('')
  const [busy, setBusy] = useState(false)
  const [tq, setTq] = useState('')
  const [tf, setTf] = useState('all')

  useEffect(() => {
    void loadSamples()
  }, [loadSamples])

  useEffect(() => {
    void loadTasks()
  }, [loadTasks])

  /** 一键载入样例：建任务 -> 提交源码 -> 直接跑完诊断。 */
  async function loadSample(id: string) {
    setBusy(true)
    try {
      await loadDemo(id)
      // 若等待期间用户已点另一个样例（demoSampleId 被接管），
      // 不要用旧样例内容覆盖输入框。
      if (useStore.getState().demoSampleId !== id) return
      const s = await api.getSample(id)
      setCode(s.content)
      setSymptom(s.symptom)
    } finally {
      setBusy(false)
    }
  }

  // 按案例分组：故障版与对照版成对，便于现场演示"命中/不误报"两面
  const groups: Record<string, { fault?: Sample; fixed?: Sample }> = {}
  for (const s of samples) {
    const g = (groups[s.case] ??= {})
    if (s.variant === 'fault') g.fault = s
    else g.fixed = s
  }
  const caseIds = Object.keys(groups).sort()

  // 历史任务搜索 + 状态筛选（客户端过滤，任务最多 50 条，量级无需后端分页）
  const q = tq.trim().toLowerCase()
  const filteredTasks = tasks.filter((t) => {
    const okQ =
      !q ||
      t.title.toLowerCase().includes(q) ||
      (t.symptom_text || '').toLowerCase().includes(q) ||
      t.id.toLowerCase().includes(q)
    const okF = tf === 'all' || t.status === tf
    return okQ && okF
  })

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-3">
      <Card title="任务">
        <div className="space-y-2">
          <input
            className="w-full rounded border border-ink-200 px-2 py-1.5 text-[13px] outline-none focus:border-ink-400"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="任务标题"
          />
          <textarea
            className="h-20 w-full resize-none rounded border border-ink-200 px-2 py-1.5 text-[13px] outline-none focus:border-ink-400"
            value={symptom}
            onChange={(e) => setSymptom(e.target.value)}
            placeholder="现象描述（例如：串口完全没有输出，但板载LED还在闪烁）"
          />
          <button
            className="w-full rounded bg-ink-800 px-3 py-1.5 text-[13px] font-medium text-white hover:bg-ink-700 disabled:opacity-50"
            disabled={busy}
            onClick={async () => {
              setBusy(true)
              try {
                await newTask(title, symptom)
              } finally {
                setBusy(false)
              }
            }}
          >
            新建任务
          </button>
        </div>
      </Card>

      {task && (
        <Card
          title="当前任务"
          extra={<span className="font-mono text-[11px] text-ink-400">{task.id}</span>}
        >
          <div className="space-y-1 text-[12px] text-ink-600">
            <div className="font-medium text-ink-800">{task.title}</div>
            <div>芯片：{task.chip}</div>
            <div>PCLK2：{task.pclk2_hz ? `${task.pclk2_hz / 1e6} MHz` : '未设置'}</div>
            <div>状态：{task.status}</div>
          </div>
        </Card>
      )}

      <Card
        title="内置示例"
        extra={<span className="text-[10px] text-ink-400">点一下即载入并诊断</span>}
      >
        {samples.length === 0 ? (
          <div className="text-[12px] text-ink-400">后端未返回示例</div>
        ) : (
          <div className="space-y-2">
            {caseIds.map((cid) => {
              const g = groups[cid]
              return (
                <div key={cid}>
                  <div className="mb-1 flex items-center gap-1.5">
                    <span className="font-mono text-[11px] font-semibold text-ink-700">
                      {cid}
                    </span>
                    <span className="text-[10px] text-ink-400">
                      {g.fault?.expect_rules?.length
                        ? `预期命中 ${g.fault.expect_rules.join(' / ')}`
                        : '预期无违规'}
                    </span>
                  </div>
                  <div className="space-y-1">
                    {[g.fault, g.fixed].map(
                      (s) =>
                        s && (
                          <SampleButton
                            key={s.id}
                            s={s}
                            active={demoSampleId === s.id}
                            busy={busy}
                            onClick={() => loadSample(s.id)}
                          />
                        ),
                    )}
                  </div>
                </div>
              )
            })}
            <div className="rounded bg-amber-50 px-2 py-1 text-[10px] leading-4 text-amber-700">
              示例均为人为注入的自撰故障，标 [演示数据]；
              「对照版」用于验证系统不会误报，不是真实板卡现象。
            </div>
          </div>
        )}
      </Card>

      {task && (
        <Card title="提交源码材料">
          <div className="space-y-2">
            <textarea
              className="h-40 w-full resize-none rounded border border-ink-200 px-2 py-1.5 font-mono text-[11px] outline-none focus:border-ink-400"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="粘贴 main.c 源码"
            />
            <div className="flex gap-2">
              <button
                className="flex-1 rounded border border-ink-300 px-2 py-1.5 text-[12px] hover:bg-ink-50"
                disabled={!code.trim()}
                onClick={() => void addSource(code)}
              >
                提交材料
              </button>
              <button
                className="flex-1 rounded bg-ink-800 px-2 py-1.5 text-[12px] font-medium text-white hover:bg-ink-700 disabled:opacity-50"
                disabled={streaming || !task}
                onClick={() => void runDiagnose()}
              >
                {streaming ? '诊断中…' : '开始诊断'}
              </button>
            </div>
          </div>
        </Card>
      )}

      {task && hwEvidence.length >= 0 && (
        <Card title={`硬件实测记录（${hwEvidence.length}）`}>
          <div className="space-y-2">
            <textarea
              className="h-24 w-full resize-none rounded border border-ink-200 px-2 py-1.5 font-mono text-[11px] outline-none focus:border-ink-400"
              value={capture}
              onChange={(e) => setCapture(e.target.value)}
              placeholder="粘贴串口助手 / Web Serial 抓到的原始帧（真实采集，勿手敲）"
            />
            <div className="flex gap-2">
              <select
                className="flex-1 rounded border border-ink-200 px-1.5 py-1 text-[12px] outline-none"
                value={capCase}
                onChange={(e) => setCapCase(e.target.value)}
              >
                <option value="">不关联案例</option>
                {['C1', 'C2', 'C3', 'C4', 'C5', 'C6'].map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
              <button
                className="flex-1 rounded border border-ink-300 px-2 py-1 text-[12px] hover:bg-ink-50 disabled:opacity-40"
                disabled={!capture.trim()}
                onClick={async () => {
                  setBusy(true)
                  try {
                    await submitSerialCapture(capture, { case_id: capCase })
                    setCapture('')
                  } finally {
                    setBusy(false)
                  }
                }}
              >
                提交抓帧并重诊断
              </button>
            </div>
            {hwEvidence.length === 0 ? (
              <div className="text-[11px] text-ink-400">
                尚无实测记录：此时全部结论只到 [静态分析]，系统只看过代码
              </div>
            ) : (
              <ul className="space-y-1">
                {hwEvidence.map((h) => (
                  <li
                    key={h.id}
                    className="flex items-center justify-between rounded border border-ink-200 px-2 py-1 text-[11px]"
                  >
                    <span className="font-mono text-ink-600">
                      {h.case_id || '—'} · {h.verdict || '未判定'}
                    </span>
                    <button
                      className="text-[10px] text-rose-500 hover:underline"
                      onClick={() => void removeHwEvidence(h.id)}
                    >
                      删除
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Card>
      )}

      {artifacts.length > 0 && (
        <Card title={`已提交材料（${artifacts.length}）`}>
          <ul className="space-y-1 text-[12px]">
            {artifacts.map((a) => (
              <li key={a.id} className="flex items-center justify-between text-ink-600">
                <span className="font-mono">{a.name}</span>
                <span className="text-ink-400">{a.size_bytes} B</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {tasks.length > 0 && (
        <Card title={`历史任务（${tasks.length}）`}>
          <div className="space-y-2">
            <input
              className="w-full rounded border border-ink-200 px-2 py-1.5 text-[12px] outline-none focus:border-ink-400"
              value={tq}
              onChange={(e) => setTq(e.target.value)}
              placeholder="搜索标题 / 现象 / 任务 ID"
            />
            <select
              className="w-full rounded border border-ink-200 px-1.5 py-1 text-[12px] outline-none"
              value={tf}
              onChange={(e) => setTf(e.target.value)}
            >
              {TASK_STATUS_FILTERS.map((f) => (
                <option key={f.value} value={f.value}>
                  {f.label}
                </option>
              ))}
            </select>
            {filteredTasks.length === 0 ? (
              <div className="text-[11px] text-ink-400">没有符合条件的任务</div>
            ) : (
              <ul className="space-y-1">
                {filteredTasks.map((t) => (
                  <li key={t.id}>
                    <button
                      onClick={() => void useStore.getState().selectTask(t.id)}
                      className={`w-full rounded px-2 py-1.5 text-left hover:bg-ink-50 ${
                        task?.id === t.id ? 'bg-ink-100' : ''
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span
                          className={`truncate text-[12px] ${
                            task?.id === t.id ? 'font-medium text-ink-900' : 'text-ink-700'
                          }`}
                        >
                          {t.title}
                        </span>
                        <span
                          className={`shrink-0 rounded px-1 text-[10px] ${
                            TASK_STATUS_STYLE[t.status] ?? 'bg-ink-100 text-ink-500'
                          }`}
                        >
                          {TASK_STATUS_LABEL[t.status] ?? t.status}
                        </span>
                      </div>
                      <div className="truncate text-[10px] text-ink-400">
                        {t.symptom_text || '（无现象描述）'}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Card>
      )}
    </div>
  )
}

/** 单个样例按钮。故障版与对照版在视觉上必须能一眼区分。 */
function SampleButton({
  s,
  active,
  busy,
  onClick,
}: {
  s: Sample
  active: boolean
  busy: boolean
  onClick: () => void
}) {
  const isFault = s.variant === 'fault'
  return (
    <button
      disabled={busy}
      onClick={onClick}
      className={`w-full rounded border px-2 py-1.5 text-left text-[12px] disabled:opacity-50 ${
        active
          ? 'border-ink-500 bg-ink-100'
          : 'border-ink-200 hover:border-ink-400 hover:bg-ink-50'
      }`}
    >
      <div className="flex items-center gap-1.5">
        <span
          className={`rounded px-1 font-mono text-[10px] ${
            isFault
              ? 'bg-rose-50 text-rose-600'
              : 'bg-emerald-50 text-emerald-700'
          }`}
        >
          {isFault ? '故障版' : '对照版'}
        </span>
        <span className="truncate font-medium text-ink-800">{s.title}</span>
      </div>
      <div className="mt-0.5 text-[11px] leading-4 text-ink-500">{s.note}</div>
    </button>
  )
}
