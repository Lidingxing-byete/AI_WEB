import { useState, type ReactNode } from 'react'
import { useStore } from '../store'
import { Card, EvidenceTag } from './EvidenceTag'
import { LlmPanel } from './LlmPanel'
import { TOOL_LABEL, ToolPanel } from './ToolPanel'
import { ReportPanel } from './ReportPanel'

/**
 * 中栏：诊断过程时间线 + 无法判定的项 + 检索到的知识卡片。
 *
 * 时间线的每一条都来自后端 SSE 事件，对应引擎真实执行的一步，
 * **不由前端伪造**。
 *
 * Phase 7 第二批：新增「诊断报告」页签，渲染后端生成的 Markdown 报告。
 */
export function CenterPanel() {
  const [view, setView] = useState<'process' | 'report'>('process')
  const { events, streaming, unknowns, references, trace, runStats, phenomenon, phenomMatches } =
    useStore()

  const steps = events.filter((e) => e.type === 'step_done')
  const done = events.find((e) => e.type === 'done')

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-1 border-b border-ink-200 bg-white px-3 pt-1">
        <TabBtn active={view === 'process'} onClick={() => setView('process')}>
          诊断过程
        </TabBtn>
        <TabBtn active={view === 'report'} onClick={() => setView('report')}>
          诊断报告
        </TabBtn>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {view === 'report' ? (
          <ReportPanel />
        ) : (
          <div className="flex flex-col gap-3">
      {phenomenon && (
        <Card
          title="串口实测现象"
          extra={
            <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-700">
              {phenomenon.verdict}
            </span>
          }
        >
          <div className="space-y-1.5">
            <div className="text-[12px] text-ink-700">{phenomenon.note}</div>
            <div className="text-[11px] text-ink-500">
              采集：{phenomenon.captured_by || '未注明'} · {phenomenon.lines} 行 /{' '}
              {phenomenon.bytes_total} 字节
            </div>
            <div className="text-[11px] text-ink-500">
              判读标 <EvidenceTag tag="[实测-硬件]" />（数据来自真实采集）；
              由现象引出的成因方向标 <EvidenceTag tag="[推断]" />
            </div>
            {phenomMatches.length > 0 && (
              <table className="mt-1 w-full text-left text-[11px]">
                <thead className="text-ink-400">
                  <tr>
                    <th className="py-1">案例</th>
                    <th>预期</th>
                    <th>实测</th>
                    <th>一致性</th>
                  </tr>
                </thead>
                <tbody>
                  {phenomMatches.map((m) => (
                    <tr key={m.case_id} className="border-t border-ink-100">
                      <td className="py-1 font-mono">{m.case_id}</td>
                      <td className="font-mono text-ink-500">{m.expected_tx}</td>
                      <td className="font-mono text-ink-500">{m.actual_tx}</td>
                      <td className={m.consistent ? 'text-emerald-600' : 'text-rose-600'}>
                        {m.consistent ? '一致（未证伪）' : '不一致（可排除）'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </Card>
      )}

      <Card
        title="诊断过程"
        extra={
          streaming ? (
            <span className="flex items-center gap-1 text-[11px] text-ink-500">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />
              执行中
            </span>
          ) : done ? (
            <span className="text-[11px] text-ink-500">
              候选 {done.data.hypothesis_count} · 无法判定 {done.data.unknown_count}
              {runStats ? ` · 工具 ${runStats.called}/${runStats.total}` : ''}
              {runStats?.error ? ` · 失败 ${runStats.error}` : ''}
            </span>
          ) : null
        }
      >
        {steps.length === 0 && trace.length === 0 && (
          <div className="py-6 text-center text-[12px] text-ink-400">
            尚未开始诊断
          </div>
        )}

        {steps.length === 0 && trace.length > 0 && (
          <ol className="space-y-2">
            {trace.map((t) => (
              <StepRow
                key={t.seq}
                seq={t.seq}
                tool={t.tool_name}
                status={t.status}
                summary={t.summary || t.reason}
              />
            ))}
          </ol>
        )}

        {steps.length > 0 && (
          <ol className="space-y-2">
            {steps.map((e) => (
              <StepRow
                key={e.id}
                seq={e.data.seq}
                tool={e.data.tool}
                status={e.data.status}
                summary={e.data.summary || e.data.reason}
                live
                durationMs={e.data.duration_ms}
                inputSum={e.data.input_sum}
                error={e.data.error}
              />
            ))}
          </ol>
        )}
      </Card>

      <Card
        title="无法判定的项"
        extra={
          <span className="text-[11px] text-ink-400">
            这些项不会包装成结论
          </span>
        }
      >
        {unknowns.length === 0 ? (
          <div className="text-[12px] text-ink-400">无</div>
        ) : (
          <ul className="space-y-1.5">
            {unknowns.map((u, i) => (
              <li key={i} className="rounded border border-ink-200 bg-ink-50 px-2 py-1.5">
                <div className="text-[12px] text-ink-700">{u.item}</div>
                {u.need && (
                  <div className="mt-0.5 text-[11px] text-ink-500">需要：{u.need}</div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <ToolPanel />

      <LlmPanel />

      <Card title={`参考知识卡片（${references.length}）`}>
        {references.length === 0 ? (
          <div className="text-[12px] text-ink-400">无命中</div>
        ) : (
          <ul className="space-y-2">
            {references.map((r) => (
              <li key={r.card_id} className="rounded border border-ink-200 px-2 py-1.5">
                <div className="flex items-start justify-between gap-2">
                  <span className="text-[12px] font-medium text-ink-800">{r.title}</span>
                  <span className="shrink-0 font-mono text-[10px] text-ink-400">
                    {r.score.toFixed(2)}
                  </span>
                </div>
                <div className="mt-0.5 text-[11px] text-ink-500">
                  来源：{r.source_ref}
                </div>
                {r.matched_terms && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {(Array.isArray(r.matched_terms)
                      ? r.matched_terms
                      : (r.matched_terms ?? '').split(',')
                    )
                      .filter(Boolean)
                      .slice(0, 8)
                      .map((t) => (
                        <span
                          key={t}
                          className="rounded bg-amber-50 px-1 font-mono text-[10px] text-amber-700"
                        >
                          {t}
                        </span>
                      ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
          </div>
        )}
      </div>
    </div>
  )
}

/** 中栏顶部页签。 */
function TabBtn({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className={`border-b-2 px-3 py-2 text-[12px] font-medium ${
        active
          ? 'border-ink-800 text-ink-900'
          : 'border-transparent text-ink-400 hover:text-ink-600'
      }`}
    >
      {children}
    </button>
  )
}

function StepRow({
  seq,
  tool,
  status,
  summary,
  live,
  durationMs,
  inputSum,
  error,
}: {
  seq: number
  tool: string
  status: string
  summary: string
  live?: boolean
  durationMs?: number
  inputSum?: string
  error?: string | null
}) {
  const skipped = status === 'skipped'
  const failed = status === 'error'
  const dot = failed
    ? 'bg-rose-500'
    : skipped
      ? 'bg-slate-300'
      : 'bg-emerald-500'
  return (
    <li className="flex gap-2.5">
      <div className="flex flex-col items-center">
        <span
          className={`mt-1 h-2 w-2 shrink-0 rounded-full ${dot} ${live ? 'animate-pulse' : ''}`}
        />
        <span className="mt-1 w-px flex-1 bg-ink-200" />
      </div>
      <div className="pb-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-mono text-[12px] text-ink-800">
            {TOOL_LABEL[tool] ?? tool}
          </span>
          <span className="font-mono text-[10px] text-ink-400">{tool}</span>
          {skipped && (
            <span className="rounded bg-slate-100 px-1 text-[10px] text-slate-500">
              跳过（未执行）
            </span>
          )}
          {failed && (
            <span className="rounded bg-rose-100 px-1 text-[10px] text-rose-600">
              执行失败
            </span>
          )}
          {typeof durationMs === 'number' && !skipped && (
            <span className="font-mono text-[10px] text-ink-400">{durationMs} ms</span>
          )}
        </div>
        <div className="mt-0.5 text-[11px] text-ink-600">{summary}</div>
        {inputSum && (
          <div className="mt-0.5 font-mono text-[10px] text-ink-400">
            输入：{inputSum}
          </div>
        )}
        {error && (
          <div className="mt-0.5 font-mono text-[10px] text-rose-600">
            {error}
          </div>
        )}
      </div>
    </li>
  )
}
