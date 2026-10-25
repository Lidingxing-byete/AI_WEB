import { useStore } from '../store'
import { Card, EvidenceTag } from './EvidenceTag'

/**
 * 模型解读面板（T-08）。
 *
 * 三条纪律在 UI 上必须可见，否则用户会误以为这一段是系统结论：
 *  1. 模型**不参与根因判定**——它只解释已查出的结论、补充验证建议。
 *  2. 输出恒为 `[推断]`，既不是实测也不是静态检查结论。
 *  3. 模型不可用 / 输出越权 → **降级**，这里必须原样显示原因，
 *     而不是悄悄消失或假装成功。
 */
export function LlmPanel() {
  const { llmNarrative, llmStatus, useLlm, setUseLlm, llmPreferred,
          setLlmPreferred, streaming } = useStore()

  const online = llmStatus?.available ?? false
  const model = llmNarrative?.model || llmStatus?.model || '未接入'
  const cloud = llmStatus?.cloud

  const PROVIDERS: { value: 'auto' | 'local' | 'cloud'; label: string }[] = [
    { value: 'auto', label: '自动（云端优先）' },
    { value: 'local', label: '本地 Ollama' },
    { value: 'cloud', label: '云端 DeepSeek' },
  ]

  return (
    <Card
      title="模型解读（可选增强）"
      extra={
        <label className="flex items-center gap-1 text-[11px] text-ink-500">
          <input
            type="checkbox"
            checked={useLlm}
            disabled={streaming}
            onChange={(e) => setUseLlm(e.target.checked)}
            className="h-3 w-3 accent-emerald-600"
          />
          启用
        </label>
      }
    >
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              online ? 'bg-emerald-500' : 'bg-slate-300'
            }`}
          />
          <span className="font-mono text-ink-700">{model}</span>
          <span className="text-ink-400">
            {llmStatus ? (online ? '本地在线' : `离线：${llmStatus.reason}`) : '状态未知'}
          </span>
          {llmNarrative?.duration_ms ? (
            <span className="font-mono text-ink-400">
              {llmNarrative.duration_ms} ms
            </span>
          ) : null}
        </div>

        {/* 模型来源切换：自动 / 本地 Ollama / 云端 DeepSeek */}
        <div className="flex flex-wrap items-center gap-2 text-[11px]">
          <span className="text-ink-400">模型来源</span>
          {PROVIDERS.map((p) => (
            <label
              key={p.value}
              className="flex cursor-pointer items-center gap-1 text-ink-600"
            >
              <input
                type="radio"
                name="llm-provider"
                value={p.value}
                checked={llmPreferred === p.value}
                disabled={streaming}
                onChange={() => setLlmPreferred(p.value)}
                className="h-3 w-3 accent-blue-600"
              />
              {p.label}
            </label>
          ))}
        </div>

        {/* 云端状态（未配置时如实显示，不假装已接入） */}
        {cloud && (
          <div
            className={`rounded border px-2 py-1.5 text-[11px] ${
              cloud.available
                ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                : 'border-ink-200 bg-ink-50 text-ink-500'
            }`}
          >
            <span className="font-mono">{cloud.provider}</span>
            <span className="mx-1.5">·</span>
            <span className="font-mono">{cloud.model}</span>
            <span className="mx-1.5">·</span>
            {cloud.configured
              ? cloud.available
                ? '云端在线'
                : `云端不可达：${cloud.reason}`
              : '云端未配置（API 密钥留空，自动走本地/规则层）'}
          </div>
        )}

        {!useLlm && (
          <div className="text-[11px] text-ink-400">
            已关闭模型解读，本轮为纯规则层诊断（结论不受模型影响）。
          </div>
        )}

        {useLlm && !llmNarrative && (
          <div className="text-[11px] text-ink-400">
            {streaming ? '等待模型…' : '本轮暂无模型输出'}
          </div>
        )}

        {llmNarrative?.degraded && (
          <div className="rounded border border-amber-200 bg-amber-50 px-2 py-1.5">
            <div className="text-[11px] font-medium text-amber-800">
              已降级为规则层结论
            </div>
            <div className="mt-0.5 text-[11px] text-amber-700">
              {llmNarrative.reason || '模型不可用'}
            </div>
          </div>
        )}

        {llmNarrative?.available && llmNarrative.summary && (
          <>
            <div className="rounded border border-ink-200 px-2 py-1.5">
              <div className="mb-1 flex items-center gap-1.5">
                <EvidenceTag tag={llmNarrative.tag || '[推断]'} />
                <span className="text-[10px] text-ink-400">
                  模型基于已查出结论的解释，非实测、非静态检查
                </span>
              </div>
              <div className="text-[12px] leading-relaxed text-ink-700">
                {llmNarrative.summary}
              </div>
            </div>

            {llmNarrative.extra_steps.length > 0 && (
              <div>
                <div className="mb-1 text-[11px] font-medium text-ink-700">
                  模型补充的验证建议（待你执行，尚未验证）
                </div>
                <ol className="space-y-1">
                  {llmNarrative.extra_steps.map((s, i) => (
                    <li key={i} className="text-[11px] text-ink-600">
                      {i + 1}. {s}
                    </li>
                  ))}
                </ol>
              </div>
            )}

            {llmNarrative.cautions.length > 0 && (
              <div>
                <div className="mb-1 text-[11px] font-medium text-ink-700">
                  注意事项
                </div>
                <ul className="space-y-1">
                  {llmNarrative.cautions.map((s, i) => (
                    <li key={i} className="text-[11px] text-ink-600">
                      · {s}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}

        <div className="border-t border-ink-100 pt-1.5 text-[10px] leading-relaxed text-ink-400">
          模型只做解释与补充建议，**不参与候选根因判定与评分**；
          输出先过证据纪律守卫（禁止声称实测/已验证、禁止标注证据等级），
          未通过即整段作废。
        </div>
      </div>
    </Card>
  )
}
