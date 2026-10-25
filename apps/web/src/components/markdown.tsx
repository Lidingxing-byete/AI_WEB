/**
 * 轻量 Markdown 渲染器（仅支持诊断报告所需的子集）。
 *
 * 依赖白名单约束：不引入任何第三方 Markdown 库，直接解析为 React 节点。
 * 支持：标题 / 段落 / 无序与有序列表（含嵌套）/ 引用 / 表格 / 代码块 /
 * 行内代码（含证据标签识别）/ 加粗。
 *
 * 证据标签识别：报告中的 `[实测-硬件]` 等以反引号包裹出现，
 * 渲染成与全站一致的 EvidenceTag 徽章，而不是普通等宽文本。
 */

import { Fragment, type ReactNode } from 'react'
import { EvidenceTag } from './EvidenceTag'

/** 报告里反引号包裹的证据标签显示文本 -> EvidenceTag 机器短码。 */
const TAG_BY_DISPLAY: Record<string, string> = {
  '[实测-硬件]': 'HW_TEST',
  '[实测-仿真]': 'SIM_TEST',
  '[静态分析]': 'STATIC',
  '[推断]': 'INFER',
  '[假设]': 'ASSUMPTION',
  '[待验证]': 'UNVERIFIED',
}

/** 报告里偶尔直接出现机器短码（如候选标题后的 `STATIC`），同样识别为徽章。 */
const TAG_BY_CODE: Record<string, string> = {
  STATIC: 'STATIC',
  SIM_TEST: 'SIM_TEST',
  HW_TEST: 'HW_TEST',
  INFER: 'INFER',
  ASSUMPTION: 'ASSUMPTION',
  UNVERIFIED: 'UNVERIFIED',
}

/** 行内渲染：行内代码（证据标签 -> 徽章）、加粗、普通文本。 */
function renderInline(text: string, keyBase: string): ReactNode[] {
  const nodes: ReactNode[] = []
  // 第一遍：切出行内代码（反引号），证据标签识别为徽章
  const parts = text.split(/`([^`]+)`/g)
  parts.forEach((part, idx) => {
    if (idx % 2 === 1) {
      const code = part.trim()
      const tag = TAG_BY_DISPLAY[code] ?? TAG_BY_CODE[code]
      nodes.push(
        tag ? (
          <EvidenceTag key={`${keyBase}-c${idx}`} tag={tag} />
        ) : (
          <code
            key={`${keyBase}-c${idx}`}
            className="rounded bg-ink-100 px-1 py-0.5 font-mono text-[11px] text-ink-800"
          >
            {code}
          </code>
        ),
      )
      return
    }
    // 第二遍：加粗
    const boldParts = part.split(/(\*\*[^*]+\*\*)/g)
    boldParts.forEach((bp, j) => {
      if (!bp) return
      if (bp.length > 4 && bp.startsWith('**') && bp.endsWith('**')) {
        nodes.push(
          <strong key={`${keyBase}-b${idx}-${j}`} className="font-semibold text-ink-900">
            {bp.slice(2, -2)}
          </strong>,
        )
      } else {
        nodes.push(<Fragment key={`${keyBase}-t${idx}-${j}`}>{bp}</Fragment>)
      }
    })
  })
  return nodes
}

interface BulletItem {
  level: number
  text: string
}

interface BulletNode extends BulletItem {
  children: BulletNode[]
}

/** 无序列表渲染：按缩进层级构建嵌套结构。 */
function renderBullets(items: BulletItem[], keyBase: string): ReactNode {
  const root: BulletNode[] = []
  const stack: BulletNode[] = []
  for (const it of items) {
    const node: BulletNode = { level: it.level, text: it.text, children: [] }
    while (stack.length > 0 && stack[stack.length - 1].level >= it.level) stack.pop()
    if (stack.length > 0) stack[stack.length - 1].children.push(node)
    else root.push(node)
    stack.push(node)
  }
  const walk = (nodes: BulletNode[]): ReactNode => (
    <ul className="space-y-1">
      {nodes.map((n, idx) => (
        <li key={`${keyBase}-${idx}`} className="text-[12px] leading-relaxed text-ink-700">
          <span>{renderInline(n.text, `${keyBase}-${idx}`)}</span>
          {n.children.length > 0 && <div className="mt-1">{walk(n.children)}</div>}
        </li>
      ))}
    </ul>
  )
  return walk(root)
}

/**
 * 块级解析入口。把报告 Markdown 渲染为一组 React 节点。
 * 只做展示层解析，不执行任何 HTML / 脚本注入（原样文本输出）。
 */
export function renderMarkdown(md: string): ReactNode {
  const lines = md.split('\n')
  const out: ReactNode[] = []
  let i = 0
  let k = 0

  while (i < lines.length) {
    const line = lines[i]
    const t = line.trim()
    if (!t) {
      i++
      continue
    }

    // 代码块
    if (t.startsWith('```')) {
      const buf: string[] = []
      i++
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        buf.push(lines[i])
        i++
      }
      i++
      out.push(
        <pre
          key={k++}
          className="overflow-x-auto rounded bg-ink-950 px-3 py-2.5 font-mono text-[11px] leading-5 text-ink-100"
        >
          {buf.join('\n')}
        </pre>,
      )
      continue
    }

    // 标题
    const h = /^(#{1,4})\s+(.*)$/.exec(t)
    if (h) {
      const lvl = h[1].length
      const cls =
        lvl === 1
          ? 'text-[16px] font-bold text-ink-900'
          : lvl === 2
            ? 'mt-1 border-b border-ink-200 pb-1 text-[14px] font-semibold text-ink-900'
            : lvl === 3
              ? 'text-[13px] font-semibold text-ink-800'
              : 'text-[12px] font-semibold text-ink-700'
      out.push(
        <div key={k++} className={cls}>
          {renderInline(h[2], `h${k}`)}
        </div>,
      )
      i++
      continue
    }

    // 引用块
    if (t.startsWith('>')) {
      const buf: string[] = []
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        buf.push(lines[i].trim().replace(/^>\s?/, ''))
        i++
      }
      out.push(
        <blockquote
          key={k++}
          className="rounded-l border-l-2 border-amber-300 bg-amber-50/70 px-3 py-1.5 text-[11px] leading-relaxed text-ink-600"
        >
          {renderInline(buf.join(' '), `q${k}`)}
        </blockquote>,
      )
      continue
    }

    // 表格
    if (t.startsWith('|')) {
      const rows: string[][] = []
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        const cells = lines[i]
          .trim()
          .replace(/^\|/, '')
          .replace(/\|$/, '')
          .split('|')
          .map((c) => c.trim())
        rows.push(cells)
        i++
      }
      let header: string[] = []
      let body: string[][] = []
      if (rows.length >= 2 && rows[1].every((c) => /^-{2,}$/.test(c) || c === '')) {
        header = rows[0]
        body = rows.slice(2)
      } else {
        body = rows
      }
      out.push(
        <div key={k++} className="overflow-x-auto rounded border border-ink-200">
          <table className="w-full text-left text-[11px]">
            {header.length > 0 && (
              <thead>
                <tr className="border-b border-ink-200 bg-ink-50">
                  {header.map((c, idx) => (
                    <th
                      key={idx}
                      className="whitespace-nowrap px-2 py-1.5 font-medium text-ink-600"
                    >
                      {renderInline(c, `th${k}-${idx}`)}
                    </th>
                  ))}
                </tr>
              </thead>
            )}
            <tbody>
              {body.map((r, idx) => (
                <tr key={idx} className="border-b border-ink-100 last:border-0">
                  {r.map((c, j) => (
                    <td key={j} className="px-2 py-1.5 align-top text-ink-700">
                      {renderInline(c, `td${k}-${idx}-${j}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      )
      continue
    }

    // 无序列表（含嵌套）
    if (/^\s*[-*]\s/.test(line) || /^\s*[-*]$/.test(t)) {
      const items: BulletItem[] = []
      while (i < lines.length) {
        const l = lines[i]
        if (/^\s*[-*]\s/.test(l) || /^\s*[-*]$/.test(l.trim())) {
          const m = /^(\s*)[-*]\s*(.*)$/.exec(l)
          if (m) {
            items.push({ level: Math.floor(m[1].length / 2), text: m[2] })
          }
          i++
        } else {
          break
        }
      }
      out.push(<div key={k++}>{renderBullets(items, `ul${k}`)}</div>)
      continue
    }

    // 有序列表（吸收后续缩进的子弹项作为上一项的子项，如"实测回填"）
    if (/^\s*\d+\.\s/.test(line)) {
      const items: { num: string; text: string; children: BulletItem[] }[] = []
      while (i < lines.length) {
        const l = lines[i]
        const m = /^\s*(\d+)\.\s*(.*)$/.exec(l)
        if (m) {
          items.push({ num: m[1], text: m[2], children: [] })
          i++
          continue
        }
        if (/^\s{2,}[-*]\s/.test(l) || /^\s{2,}[-*]$/.test(l.trim())) {
          const cm = /^\s*[-*]\s*(.*)$/.exec(l.trim())
          if (cm && items.length > 0) {
            items[items.length - 1].children.push({ level: 1, text: cm[1] })
          }
          i++
          continue
        }
        break
      }
      out.push(
        <ol key={k++} className="space-y-1">
          {items.map((it, idx) => (
            <li key={idx} className="text-[12px] leading-relaxed text-ink-700">
              <span>
                {it.num}. {renderInline(it.text, `ol${k}-${idx}`)}
              </span>
              {it.children.length > 0 && (
                <div className="mt-1">{renderBullets(it.children, `olc${k}-${idx}`)}</div>
              )}
            </li>
          ))}
        </ol>,
      )
      continue
    }

    // 普通段落：收集连续的非特殊行
    const buf: string[] = []
    while (i < lines.length) {
      const l = lines[i].trim()
      if (!l) break
      if (l.startsWith('```') || l.startsWith('>') || l.startsWith('|')) break
      if (/^(#{1,4})\s/.test(l)) break
      if (/^\s*[-*]\s/.test(lines[i]) || /^\s*[-*]$/.test(l)) break
      if (/^\s*\d+\.\s/.test(lines[i])) break
      buf.push(l)
      i++
    }
    out.push(
      <p key={k++} className="text-[12px] leading-relaxed text-ink-700">
        {renderInline(buf.join(' '), `p${k}`)}
      </p>,
    )
  }

  return out
}
