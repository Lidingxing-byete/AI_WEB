import { useEffect } from 'react'
import { CenterPanel } from './components/CenterPanel'
import { LeftPanel } from './components/LeftPanel'
import { RightPanel } from './components/RightPanel'
import { EvidenceTag, TagLegend } from './components/EvidenceTag'
import { useStore } from './store'

export default function App() {
  const {
    task,
    error,
    clearError,
    backendUp,
    taskLoading,
    checkHealth,
    loadTools,
    loadSamples,
    loadLlmStatus,
    tools,
    llmStatus,
    hypotheses,
    streaming,
  } = useStore()

  // 启动自检：后端连通性、工具清单、内置样例、模型层状态。
  // 任一拿不到都如实显示，不用假数据填充界面。
  useEffect(() => {
    void checkHealth()
    void loadTools()
    void loadSamples()
    void loadLlmStatus()
  }, [checkHealth, loadTools, loadSamples, loadLlmStatus])

  // 连通性轮询：后端中途挂掉时，顶栏不能还亮着"已连接"。
  // 20s 一次（对本地服务开销可忽略），页面重新可见时立即补一次。
  useEffect(() => {
    const timer = window.setInterval(() => void checkHealth(), 20_000)
    const onVisible = () => {
      if (document.visibilityState === 'visible') void checkHealth()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [checkHealth])

  return (
    <div className="flex h-full flex-col bg-ink-50 text-ink-900">
      <header className="flex items-center justify-between border-b border-ink-200 bg-white px-4 py-2.5">
        <div className="flex items-baseline gap-2">
          <span className="text-[15px] font-semibold">芯诊 AI</span>
          <span className="text-[11px] text-ink-500">
            证据驱动的 STM32 故障诊断工作台
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span
            className="flex items-center gap-1 text-[11px] text-ink-500"
            title="后端连通状态"
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                backendUp === null
                  ? 'bg-slate-300'
                  : backendUp
                    ? 'bg-emerald-500'
                    : 'bg-rose-500'
              }`}
            />
            {backendUp === null ? '检查中' : backendUp ? '后端已连接' : '后端未响应'}
          </span>
          <span
            className="flex items-center gap-1 text-[11px] text-ink-500"
            title={
              llmStatus
                ? `${llmStatus.model} @ ${llmStatus.host}${llmStatus.reason ? ' · ' + llmStatus.reason : ''}`
                : '模型层状态未知'
            }
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                llmStatus?.available ? 'bg-emerald-500' : 'bg-slate-300'
              }`}
            />
            {llmStatus?.available
              ? `模型 ${llmStatus.model}`
              : '模型离线（自动降级）'}
          </span>
          <TagLegend />
        </div>
      </header>

      {/* 后端不可达：明确告知 + 可重试，不让界面停在"看起来正常"的状态 */}
      {backendUp === false && (
        <div className="flex items-center justify-between border-b border-rose-200 bg-rose-50 px-4 py-1.5 text-[12px] text-rose-700">
          <span>
            无法连接后端（127.0.0.1:8787）。请确认服务已启动：
            <code className="mx-1 rounded bg-white px-1 font-mono text-[11px]">
              python -m uvicorn app.main:app --port 8787
            </code>
          </span>
          <button
            onClick={() => {
              void checkHealth()
              void loadTools()
              void loadSamples()
            }}
            className="rounded border border-rose-300 px-2 py-0.5 text-[11px] hover:bg-rose-100"
          >
            重试
          </button>
        </div>
      )}

      {/* 切换任务时立刻给反馈：否则"任务已选中但结果还没到"会被误读成"没检出" */}
      {taskLoading && (
        <div className="flex items-center gap-2 border-b border-ink-200 bg-white px-4 py-1 text-[11px] text-ink-500">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-blue-500" />
          正在载入该任务的诊断数据…
        </div>
      )}

      {error && (
        <div className="flex items-center justify-between border-b border-rose-200 bg-rose-50 px-4 py-1.5 text-[12px] text-rose-700">
          <span>{error}</span>
          <button
            onClick={clearError}
            className="ml-3 shrink-0 rounded border border-rose-300 px-2 py-0.5 text-[11px] hover:bg-rose-100"
          >
            关闭
          </button>
        </div>
      )}

      {!task && (
        <div className="flex items-center justify-center border-b border-ink-200 bg-amber-50 px-4 py-1.5 text-[12px] text-amber-700">
          请先在左栏新建任务，或点一个内置示例（会自动载入并跑完诊断）
        </div>
      )}

      {/* 只在"确实诊断过"的任务上提示未检出：
          刚建好还没跑诊断的任务不该被说成"本轮未检出"（那是误报）。 */}
      {task && task.status !== 'created' && !streaming && hypotheses.length === 0 && (
        <div className="flex items-center justify-center border-b border-ink-200 bg-ink-100 px-4 py-1.5 text-[12px] text-ink-600">
          本轮未检出配置违规。可能是材料不足（看中栏「无法判定的项」），也可能是这份代码确实没问题。
        </div>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-[320px_minmax(0,1fr)_380px] gap-3 p-3">
        <div className="min-h-0 overflow-hidden rounded-lg border border-ink-200 bg-ink-50">
          <LeftPanel />
        </div>
        <div className="min-h-0 overflow-hidden rounded-lg border border-ink-200 bg-ink-50">
          <CenterPanel />
        </div>
        <div className="min-h-0 overflow-hidden rounded-lg border border-ink-200 bg-ink-50">
          <RightPanel />
        </div>
      </div>

      <footer className="flex items-center gap-2 border-t border-ink-200 bg-white px-4 py-1.5 text-[11px] text-ink-500">
        <span>
          根因判定：<EvidenceTag tag="STATIC" /> 规则层 + 工具
          {llmStatus?.available ? (
            <>
              ；解释增强：{llmStatus.model}
              <EvidenceTag tag="INFER" />
            </>
          ) : (
            '；模型不可用，全部结论来自规则层'
          )}
        </span>
        <span className="text-ink-300">|</span>
        <span>诊断工具 {tools.length || '…'} 个，全部调用留痕可查</span>
        <span className="text-ink-300">|</span>
        <span>所有结论均标注证据等级，未见实测证据者一律不标 [实测-硬件]</span>
      </footer>
    </div>
  )
}
