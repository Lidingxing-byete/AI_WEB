/**
 * 诊断报告面板（Phase 7 第二批）。
 *
 * 报告全文由后端生成（`/api/tasks/{tid}/report`，Markdown，全文带证据标签），
 * 前端只负责：拉取 -> 轻量 Markdown 渲染 -> 「重新生成」写入新版本。
 * 前端不做任何诊断推断，也不改写报告内容。
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { useStore } from '../store'
import { Card } from './EvidenceTag'
import { renderMarkdown } from './markdown'

export function ReportPanel() {
  const task = useStore((s) => s.task)
  const [md, setMd] = useState<string | null>(null)
  const [version, setVersion] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(
    async (regenerate: boolean) => {
      if (!task) return
      setLoading(true)
      setError(null)
      try {
        const r = await api.getReport(task.id, regenerate)
        setMd(r.md_content)
        setVersion(r.version)
      } catch (e: any) {
        setError(String(e.message ?? e))
      } finally {
        setLoading(false)
      }
    },
    [task],
  )

  // 切换任务时重新拉取最新已存版本；不自动覆盖历史版本。
  useEffect(() => {
    setMd(null)
    setVersion(null)
    if (task) void load(false)
  }, [task, load])

  return (
    <Card
      title="诊断报告"
      extra={
        <div className="flex items-center gap-2">
          {version != null && (
            <span className="font-mono text-[10px] text-ink-400">v{version}</span>
          )}
          <button
            disabled={!task || loading}
            onClick={() => void load(true)}
            className="rounded border border-ink-300 px-2 py-0.5 text-[11px] hover:bg-ink-50 disabled:opacity-40"
          >
            {loading ? '生成中…' : '重新生成'}
          </button>
        </div>
      }
    >
      {!task ? (
        <div className="py-8 text-center text-[12px] text-ink-400">尚未选择任务</div>
      ) : error ? (
        <div className="rounded bg-rose-50 px-2 py-2 text-[12px] text-rose-700">{error}</div>
      ) : md === null ? (
        <div className="py-8 text-center text-[12px] text-ink-400">
          {loading ? '正在生成报告…' : '报告为空'}
        </div>
      ) : (
        <div className="space-y-3">{renderMarkdown(md)}</div>
      )}
      {md != null && (
        <div className="mt-3 border-t border-ink-100 pt-2 text-[10px] leading-4 text-ink-400">
          报告由后端按证据纪律生成，全文带证据标签；
          「重新生成」会写入新版本并保留历史版本，不删除已有内容。
        </div>
      )}
    </Card>
  )
}
