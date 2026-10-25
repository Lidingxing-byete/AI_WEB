import { useState } from 'react'
import { useStore } from '../store'
import { Card, EvidenceTag } from './EvidenceTag'

/**
 * 右栏：下一步待补充信息、候选根因、支持证据、验证步骤、实测结果回填。
 *
 * 红线：系统**不提供**任何自动填充观测值的入口。
 * `observed` 与 `match` 必须由用户填写，缺一项后端即 422。
 */
export function RightPanel() {
  const { hypotheses, streaming, submitResult } = useStore()

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-3">
      <ExpectationCard />
      <FollowupCard />
      <VerificationPlanCard />

      {hypotheses.length === 0 && !streaming && (
        <Card title="候选根因">
          <div className="py-6 text-center text-[12px] text-ink-400">
            未检出配置违规，或尚未开始诊断
          </div>
        </Card>
      )}

      {hypotheses.map((h) => (
        <HypothesisCard key={h.rule_id + h.rank} h={h} onSubmit={submitResult} />
      ))}
    </div>
  )
}

/**
 * 「预期命中 vs 实际命中」对照。
 *
 * 只在载入内置样例时出现。作用是把演示从"看起来对"变成"可核对"：
 * 样例元数据里声明了预期命中的规则号，这里直接列出漏检与多报。
 *
 * 注意：预期规则本身是**未经硬件实测**的设计声明（标 [待验证]），
 * 它只能说明"静态检查是否符合设计"，不能证明板卡现象。
 */
function ExpectationCard() {
  const { demoSampleId, samples, hypotheses } = useStore()
  const s = samples.find((x) => x.id === demoSampleId)
  if (!s) return null

  const hit = hypotheses.map((h) => h.rule_id)
  const expected = s.expect_rules ?? []
  const missed = expected.filter((r) => !hit.includes(r))
  const extra = hit.filter((r) => !expected.includes(r))

  const ok = missed.length === 0 && extra.length === 0
  return (
    <Card
      title="预期命中 vs 实际命中"
      extra={
        <span
          className={`rounded border px-1.5 py-0.5 text-[10px] ${
            ok
              ? 'border-emerald-300 bg-emerald-50 text-emerald-700'
              : 'border-amber-300 bg-amber-50 text-amber-700'
          }`}
        >
          {ok ? '与设计一致' : '存在偏差'}
        </span>
      }
    >
      <div className="space-y-1 text-[12px]">
        <div className="text-ink-600">
          预期：<span className="font-mono">{expected.join(' / ') || '无违规'}</span>
        </div>
        <div className="text-ink-600">
          实际：<span className="font-mono">{hit.join(' / ') || '未检出'}</span>
        </div>
        {missed.length > 0 && (
          <div className="text-rose-600">漏检：{missed.join(' / ')}</div>
        )}
        {extra.length > 0 && (
          <div className="text-amber-700">多报：{extra.join(' / ')}</div>
        )}
        <div className="pt-1 text-[10px] leading-4 text-ink-400">
          预期规则是样例设计声明 [待验证]，未经硬件实测；
          此处只核对静态检查是否符合设计，不代表板卡真实现象。
        </div>
      </div>
    </Card>
  )
}

/**
 * 「还需要补充什么」——多步诊断的入口。
 *
 * 系统在这一栏明确列出"要继续诊断需要你提供什么"，
 * 而不是在证据不足时硬给一个根因。
 */
function FollowupCard() {
  const { followups, task, submitObservedBaud } = useStore()
  const [baud, setBaud] = useState('')
  const [source, setSource] = useState('串口助手实测')
  const [busy, setBusy] = useState(false)

  return (
    <Card
      title="下一步需要补充的信息"
      extra={
        task?.observed_baud ? (
          <span className="font-mono text-[10px] text-emerald-700">
            已填 {task.observed_baud} bps
          </span>
        ) : (
          <span className="text-[10px] text-ink-400">补齐后可重新诊断</span>
        )
      }
    >
      {followups.length === 0 ? (
        <div className="text-[12px] text-ink-400">本轮没有必须补充的关键项</div>
      ) : (
        <ol className="mb-2 space-y-1.5">
          {followups.map((f) => (
            <li key={f.seq} className="text-[12px] text-ink-700">
              {f.seq}. {f.item}
            </li>
          ))}
        </ol>
      )}

      <div className="rounded border border-ink-200 bg-amber-50/60 px-2 py-1.5">
        <div className="mb-1 text-[11px] font-medium text-ink-700">
          实测波特率（反向比对用）
        </div>
        <div className="text-[10px] leading-4 text-ink-500">
          由你在串口助手/示波器上读出的实际波特率。系统据此反推真实总线时钟，
          该值按 [实测-硬件] 记录，反推出的时钟结论仍标 [推断]。
        </div>
        <div className="mt-1.5 flex items-center gap-1.5">
          <input
            className="w-24 rounded border border-ink-200 px-1.5 py-1 font-mono text-[11px] outline-none focus:border-ink-400"
            placeholder="12800"
            value={baud}
            onChange={(e) => setBaud(e.target.value)}
          />
          <span className="text-[10px] text-ink-400">bps</span>
          <select
            className="rounded border border-ink-200 px-1.5 py-1 text-[11px]"
            value={source}
            onChange={(e) => setSource(e.target.value)}
          >
            <option value="串口助手实测">串口助手实测</option>
            <option value="示波器位宽换算">示波器位宽换算</option>
            <option value="逻辑分析仪">逻辑分析仪</option>
          </select>
          <button
            disabled={!baud.trim() || Number.isNaN(Number(baud)) || busy}
            onClick={async () => {
              setBusy(true)
              try {
                await submitObservedBaud(Number(baud), source)
                setBaud('')
              } finally {
                setBusy(false)
              }
            }}
            className="rounded bg-ink-800 px-2 py-1 text-[11px] text-white hover:bg-ink-700 disabled:opacity-50"
          >
            提交并重新诊断
          </button>
        </div>
      </div>
    </Card>
  )
}

/** 回填判定 -> 中文。与后端 match 取值一致。 */
const MATCH_LABEL: Record<string, string> = {
  match: '与预期一致',
  mismatch: '与预期不符',
  partial: '部分符合',
  unknown: '无法判断',
}

const MATCH_STYLE: Record<string, string> = {
  match: 'bg-emerald-50 text-emerald-700',
  mismatch: 'bg-rose-50 text-rose-700',
  partial: 'bg-amber-50 text-amber-700',
  unknown: 'bg-slate-100 text-slate-600',
}

/**
 * 验证计划总览（Phase 7 第二批）。
 *
 * 跨所有候选根因汇总验证步骤：总步骤数、已完成/待执行、完成进度，
 * 并列出每一条待办步骤及其所属候选。数据全部来自已落库的
 * hypotheses.verifications（含用户回填的实测结果），前端不做推断。
 */
function VerificationPlanCard() {
  const { hypotheses, streaming } = useStore()
  const all: {
    seq: number
    action: string
    observed?: string | null
    match?: string | null
    hypothesis_title?: string | null
  }[] = []
  for (const h of hypotheses) {
    for (const v of h.verifications ?? []) {
      all.push({
        seq: v.seq,
        action: v.action,
        observed: v.observed,
        match: v.match,
        hypothesis_title: h.title,
      })
    }
  }
  all.sort((a, b) => a.seq - b.seq)

  const total = all.length
  const done = all.filter((v) => v.observed).length
  const pending = total - done
  const pct = total > 0 ? Math.round((done / total) * 100) : 0
  const byMatch = {
    match: 0,
    mismatch: 0,
    partial: 0,
    unknown: 0,
  }
  for (const v of all) {
    if (v.observed && v.match && v.match in byMatch) byMatch[v.match as keyof typeof byMatch]++
  }

  return (
    <Card
      title="验证计划总览"
      extra={
        total > 0 ? (
          <span className="font-mono text-[10px] text-ink-500">
            {done}/{total} 已完成
          </span>
        ) : (
          <span className="text-[10px] text-ink-400">
            {streaming ? '诊断中…' : '尚未生成'}
          </span>
        )
      }
    >
      {total === 0 ? (
        <div className="text-[12px] text-ink-400">
          {streaming ? '诊断结束后将在这里汇总全部候选的验证步骤' : '先跑一轮诊断，才会生成可执行的验证计划'}
        </div>
      ) : (
        <div className="space-y-2">
          {/* 进度条：完成度可视化 */}
          <div className="flex items-center gap-2">
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-ink-100">
              <div
                className="h-full rounded-full bg-emerald-500"
                style={{ width: `${pct}%` }}
              />
            </div>
            <span className="font-mono text-[10px] text-ink-500">{pct}%</span>
          </div>
          <div className="flex flex-wrap gap-1 text-[10px]">
            <span className="rounded bg-slate-100 px-1 text-slate-600">待执行 {pending}</span>
            <span className="rounded bg-emerald-50 px-1 text-emerald-700">一致 {byMatch.match}</span>
            <span className="rounded bg-rose-50 px-1 text-rose-700">不符 {byMatch.mismatch}</span>
            <span className="rounded bg-amber-50 px-1 text-amber-700">部分 {byMatch.partial}</span>
            <span className="rounded bg-slate-100 px-1 text-slate-500">未知 {byMatch.unknown}</span>
          </div>

          <ol className="space-y-1">
            {all.map((v, i) => {
              const label = v.observed
                ? MATCH_LABEL[v.match ?? ''] ?? v.match ?? '已回填'
                : '待执行'
              const style = v.observed
                ? MATCH_STYLE[v.match ?? ''] ?? 'bg-ink-100 text-ink-600'
                : 'bg-slate-100 text-slate-600'
              return (
                <li
                  key={i}
                  className="flex items-start gap-2 rounded border border-ink-200 px-2 py-1.5"
                >
                  <span className="mt-0.5 shrink-0 font-mono text-[10px] text-ink-400">
                    {v.seq}.
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="text-[12px] leading-snug text-ink-700">{v.action}</div>
                    <div className="truncate text-[10px] text-ink-400">
                      候选：{v.hypothesis_title ?? '（未关联）'}
                    </div>
                  </div>
                  <span className={`shrink-0 rounded px-1 text-[10px] ${style}`}>{label}</span>
                </li>
              )
            })}
          </ol>
          <div className="text-[10px] leading-4 text-ink-400">
            实测结果只能由你填写：在下方的候选卡片中「回填实测」，完成后本总览会实时更新。
          </div>
        </div>
      )}
    </Card>
  )
}

function HypothesisCard({
  h,
  onSubmit,
}: {
  h: any
  onSubmit: (vid: string, observed: string, match: string) => Promise<void>
}) {
  const [openFor, setOpenFor] = useState<string | null>(null)
  const [observed, setObserved] = useState('')
  const [match, setMatch] = useState('match')
  const [saving, setSaving] = useState(false)

  const statusStyle =
    h.status === 'refuted'
      ? 'bg-rose-50 text-rose-700 border-rose-300'
      : h.status === 'confirmed'
        ? 'bg-emerald-50 text-emerald-700 border-emerald-300'
        : 'bg-ink-100 text-ink-600 border-ink-300'

  const statusLabel =
    h.status === 'refuted' ? '已排除' : h.status === 'confirmed' ? '已确认' : '待验证'

  return (
    <Card
      title={
        <span className="flex items-center gap-1.5">
          <span className="font-mono text-[11px] text-ink-400">#{h.rank}</span>
          <span>{h.title}</span>
        </span>
      }
      extra={
        <span className="flex items-center gap-1.5">
          <span className={`rounded border px-1.5 py-0.5 text-[10px] ${statusStyle}`}>
            {statusLabel}
          </span>
          <EvidenceTag tag={h.evidence_tag} />
        </span>
      }
    >
      <div className="space-y-2.5">
        <div className="flex items-center gap-2 text-[11px] text-ink-500">
          <span className="font-mono">{h.rule_id}</span>
          <span>支持度 {h.score}</span>
        </div>

        {h.evidences?.length > 0 && (
          <div>
            <div className="mb-1 text-[11px] font-medium text-ink-500">支持证据</div>
            <ul className="space-y-1">
              {h.evidences.map((e: any, i: number) => (
                <li key={i} className="flex gap-1.5 text-[12px] text-ink-700">
                  <span className="mt-0.5 shrink-0">
                    <EvidenceTag tag={e.evidence_tag} />
                  </span>
                  <span>{e.item}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {h.verifications?.length > 0 && (
          <div>
            <div className="mb-1 text-[11px] font-medium text-ink-500">验证步骤</div>
            <ol className="space-y-1.5">
              {h.verifications.map((v: any) => (
                <li key={v.id} className="rounded border border-ink-200 px-2 py-1.5">
                  <div className="flex items-start justify-between gap-2">
                    <span className="text-[12px] text-ink-700">
                      {v.seq}. {v.action}
                    </span>
                    <span className="flex shrink-0 items-center gap-1">
                      {v.observed && (
                        <span className="rounded bg-emerald-50 px-1 text-[10px] text-emerald-700">
                          已回填
                        </span>
                      )}
                      {v.id && (
                        <button
                          onClick={() => setOpenFor(openFor === v.id ? null : v.id)}
                          className="rounded border border-ink-300 px-1.5 py-0.5 text-[10px] text-ink-600 hover:bg-ink-50"
                        >
                          {openFor === v.id ? '收起' : v.observed ? '重新回填' : '回填实测'}
                        </button>
                      )}
                    </span>
                  </div>

                  {/* 已回填的实测结果：重新诊断后仍在（后端 _replay_test_results 保证） */}
                  {v.observed && openFor !== v.id && (
                    <div className="mt-1.5 rounded bg-emerald-50/60 px-2 py-1">
                      <div className="flex items-center gap-1.5">
                        <EvidenceTag tag="HW_TEST" />
                        <span className="text-[10px] text-ink-500">
                          {MATCH_LABEL[v.match ?? ''] ?? v.match}
                          {v.recorded_by ? ` · ${v.recorded_by}` : ''}
                        </span>
                      </div>
                      <div className="mt-0.5 text-[11px] text-ink-700">
                        {v.observed}
                      </div>
                    </div>
                  )}

                  {openFor === v.id && (
                    <div className="mt-2 space-y-1.5 border-t border-ink-100 pt-2">
                      <div className="rounded bg-amber-50 px-2 py-1 text-[10px] text-amber-700">
                        实测结果只能由你填写，系统不会代填。提交后标签为 [实测-硬件]。
                      </div>
                      <textarea
                        className="h-16 w-full resize-none rounded border border-ink-200 px-2 py-1 text-[11px] outline-none focus:border-ink-400"
                        placeholder="观察到的实际现象（例如：串口收到 [BASELINE] tick 3，无乱码）"
                        value={observed}
                        onChange={(e) => setObserved(e.target.value)}
                      />
                      <div className="flex items-center gap-1.5">
                        <select
                          className="rounded border border-ink-200 px-1.5 py-1 text-[11px]"
                          value={match}
                          onChange={(e) => setMatch(e.target.value)}
                        >
                          <option value="match">与预期一致</option>
                          <option value="mismatch">与预期不符</option>
                          <option value="partial">部分符合</option>
                          <option value="unknown">无法判断</option>
                        </select>
                        <button
                          disabled={!observed.trim() || saving}
                          onClick={async () => {
                            setSaving(true)
                            try {
                              await onSubmit(v.id, observed, match)
                              setObserved('')
                              setOpenFor(null)
                            } finally {
                              setSaving(false)
                            }
                          }}
                          className="rounded bg-ink-800 px-2 py-1 text-[11px] text-white hover:bg-ink-700 disabled:opacity-50"
                        >
                          提交
                        </button>
                      </div>
                    </div>
                  )}
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>
    </Card>
  )
}
