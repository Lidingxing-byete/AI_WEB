import type { ReactNode } from 'react'

/**
 * 证据标签徽章。
 *
 * 颜色按**可信度**分级，而非按重要性：
 * 实测（绿/青）> 静态分析（蓝）> 推断（琥珀）> 假设（橙，需警惕）> 待验证（灰）。
 * 目标是让用户不看文字也能立刻判断"这条结论有多硬"。
 */

const STYLE: Record<string, { bg: string; fg: string; border: string; label: string; hint: string }> = {
  HW_TEST: {
    bg: 'bg-emerald-50',
    fg: 'text-emerald-700',
    border: 'border-emerald-300',
    label: '[实测-硬件]',
    hint: '用户在真实板卡上完成并提供原始记录',
  },
  SIM_TEST: {
    bg: 'bg-teal-50',
    fg: 'text-teal-700',
    border: 'border-teal-300',
    label: '[实测-仿真]',
    hint: 'Keil 仿真 / 软件模拟实际运行',
  },
  STATIC: {
    bg: 'bg-blue-50',
    fg: 'text-blue-700',
    border: 'border-blue-300',
    label: '[静态分析]',
    hint: '仅代码、配置、寄存器定义检查，未上板',
  },
  INFER: {
    bg: 'bg-amber-50',
    fg: 'text-amber-700',
    border: 'border-amber-300',
    label: '[推断]',
    hint: '基于已有证据的推理',
  },
  ASSUMPTION: {
    bg: 'bg-orange-50',
    fg: 'text-orange-700',
    border: 'border-orange-300',
    label: '[假设]',
    hint: '缺少证据时的显式声明',
  },
  UNVERIFIED: {
    bg: 'bg-slate-100',
    fg: 'text-slate-600',
    border: 'border-slate-300',
    label: '[待验证]',
    hint: '尚未取得任何证据',
  },
}

export function EvidenceTag({ tag, className = '' }: { tag: string; className?: string }) {
  const s = STYLE[tag] ?? {
    bg: 'bg-rose-50',
    fg: 'text-rose-700',
    border: 'border-rose-300',
    label: tag,
    hint: '未知标签',
  }
  return (
    <span
      title={s.hint}
      className={`inline-flex items-center rounded border px-1.5 py-0.5 font-mono text-[11px] leading-none ${s.bg} ${s.fg} ${s.border} ${className}`}
    >
      {s.label}
    </span>
  )
}

export function TagLegend() {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {Object.entries(STYLE).map(([k, v]) => (
        <span key={k} title={v.hint}>
          <EvidenceTag tag={k} />
        </span>
      ))}
    </div>
  )
}

export function Card({
  title,
  extra,
  children,
}: {
  title: ReactNode
  extra?: ReactNode
  children?: ReactNode
}) {
  return (
    <div className="rounded-lg border border-ink-200 bg-white">
      <div className="flex items-center justify-between border-b border-ink-100 px-3 py-2">
        <div className="text-[13px] font-semibold text-ink-800">{title}</div>
        {extra}
      </div>
      <div className="p-3">{children}</div>
    </div>
  )
}
