/**
 * 诊断工具面板。
 *
 * 系统能做什么，全部摊开给用户看，包括"本轮没调用的"和"调用失败的"。
 * 这是可解释性的一部分：不让人猜这个 AI 到底做了什么。
 *
 * 数据来源：`/api/tools`（能力清单）+ 本轮 `/tool-runs`（真实调用留痕）。
 * 两者都是后端真实数据，前端不做任何推断。
 */
import { useStore } from '../store'
import { Card, EvidenceTag } from './EvidenceTag'

/** 工具名 -> 中文职责名。用于展示，不是诊断依据。 */
export const TOOL_LABEL: Record<string, string> = {
  parse_build_log: '编译日志解析',
  analyze_c_code: 'C 源码解析',
  check_uart_config: '配置一致性检查',
  calc_baud_error: '波特率误差计算',
  infer_actual_fck: '实测波特率反向比对',
  analyze_isr_body: '中断服务函数体分析',
  search_reference: '知识卡片检索',
  parse_serial_capture: '串口实测抓帧解析',
  // T-08：按需注册（默认注册表里没有），因此工具清单里可能不出现。
  // 出现即表示本轮确实调用过模型。
  llm_assist: '模型解读（本地 Ollama）',
}

const STATUS_STYLE: Record<string, string> = {
  called: 'bg-emerald-50 text-emerald-700 border-emerald-300',
  skipped: 'bg-slate-100 text-slate-500 border-slate-300',
  error: 'bg-rose-50 text-rose-700 border-rose-300',
}

const STATUS_LABEL: Record<string, string> = {
  called: '已执行',
  skipped: '跳过',
  error: '失败',
}

export function ToolPanel() {
  const { tools, toolRuns, runStats } = useStore()

  const byName = new Map(toolRuns.map((r) => [r.tool_name, r]))

  return (
    <Card
      title={`诊断工具（${tools.length || '…'}）`}
      extra={
        runStats ? (
          <span className="text-[11px] text-ink-500">
            本轮执行 {runStats.called}/{runStats.total}
            {runStats.error ? ` · 失败 ${runStats.error}` : ''}
            {runStats.total_ms ? ` · ${runStats.total_ms} ms` : ''}
          </span>
        ) : (
          <span className="text-[10px] text-ink-400">尚未诊断</span>
        )
      }
    >
      {tools.length === 0 ? (
        <div className="text-[12px] text-ink-400">后端未返回工具清单</div>
      ) : (
        <ul className="space-y-1.5">
          {tools.map((t) => {
            const run = byName.get(t.name)
            const st = run?.status ?? 'idle'
            return (
              <li
                key={t.name}
                className="rounded border border-ink-200 px-2 py-1.5"
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-[12px] font-medium text-ink-800">
                    {TOOL_LABEL[t.name] ?? t.name}
                  </span>
                  <span className="font-mono text-[10px] text-ink-400">
                    {t.name}
                  </span>
                  {run ? (
                    <span
                      className={`rounded border px-1 text-[10px] ${
                        STATUS_STYLE[st] ?? 'border-ink-300 text-ink-500'
                      }`}
                    >
                      {STATUS_LABEL[st] ?? st}
                    </span>
                  ) : (
                    <span className="rounded border border-ink-200 px-1 text-[10px] text-ink-400">
                      本轮未触发
                    </span>
                  )}
                  <span className="ml-auto shrink-0">
                    <EvidenceTag tag={t.default_tag} />
                  </span>
                </div>
                <div className="mt-0.5 text-[11px] leading-4 text-ink-500">
                  {t.description}
                </div>
                {run?.error && (
                  <div className="mt-0.5 font-mono text-[10px] text-rose-600">
                    {run.error}
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}
