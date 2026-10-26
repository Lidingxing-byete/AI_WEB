/**
 * 诊断报告面板（Phase 7 第二批）。
 *
 * 报告全文由后端生成（`/api/tasks/{tid}/report`，Markdown，全文带证据标签），
 * 前端只负责：拉取 -> 轻量 Markdown 渲染 -> 「重新生成」写入新版本。
 * 前端不做任何诊断推断，也不改写报告内容。
 *
 * 便捷性（2026-10-26）：报告要能交付出去——支持「复制 Markdown」与
 * 「下载 .md」，内容与页面显示完全一致（都取后端返回的原文）。
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { useStore } from '../store'
import { Card } from './EvidenceTag'
import { renderMarkdown } from './markdown'

/** 文件名安全化：去掉 Windows/Unix 双向非法字符，避免下载到无法保存的名字。 */
function safeName(title: string): string {
  const t = (title || '诊断报告').replace(/[\\/:*?"<>|\r\n]/g, '_').trim()
  return (t || '诊断报告').slice(0, 80)
}

export function ReportPanel() {
  const task = useStore((s) => s.task)
  const [md, setMd] = useState<string | null>(null)
  const [version, setVersion] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

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

  /** 复制 Markdown 原文。剪贴板 API 不可用（非安全上下文）时退回 execCommand。 */
  async function copyMd() {
    if (md == null) return
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(md)
      } else {
        const ta = document.createElement('textarea')
        ta.value = md
        ta.style.position = 'fixed'
        ta.style.opacity = '0'
        document.body.appendChild(ta)
        ta.select()
        const ok = document.execCommand('copy')
        document.body.removeChild(ta)
        if (!ok) throw new Error('浏览器拒绝了复制命令')
      }
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch (e: any) {
      setError(`复制失败：${String(e?.message ?? e)}`)
    }
  }

  /** 下载为 .md 文件（内容 = 后端原文，不经前端改写）。 */
  function downloadMd() {
    if (md == null) return
    const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${safeName(task?.title ?? '')}-诊断报告-v${version ?? 1}.md`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  return (
    <Card
      title="诊断报告"
      extra={
        <div className="flex items-center gap-2">
          {version != null && (
            <span className="font-mono text-[10px] text-ink-400">v{version}</span>
          )}
          <button
            disabled={md == null}
            onClick={() => void copyMd()}
            title="复制报告 Markdown 原文"
            className="rounded border border-ink-300 px-2 py-0.5 text-[11px] hover:bg-ink-50 disabled:opacity-40"
          >
            {copied ? '已复制' : '复制'}
          </button>
          <button
            disabled={md == null}
            onClick={downloadMd}
            title="下载 .md 文件"
            className="rounded border border-ink-300 px-2 py-0.5 text-[11px] hover:bg-ink-50 disabled:opacity-40"
          >
            下载 .md
          </button>
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
