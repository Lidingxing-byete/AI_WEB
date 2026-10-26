import { useEffect, useMemo, useState } from 'react'
import { useStore } from '../store'
import { Card, EvidenceTag } from './EvidenceTag'

/**
 * 模型解读面板（T-08）+ 「我的云端密钥」。
 *
 * 三条纪律在 UI 上必须可见，否则用户会误以为这一段是系统结论：
 *  1. 模型**不参与根因判定**——它只解释已查出的结论、补充验证建议。
 *  2. 输出恒为 `[推断]`，既不是实测也不是静态检查结论。
 *  3. 模型不可用 / 输出越权 → **降级**，这里必须原样显示原因，
 *     而不是悄悄消失或假装成功。
 *
 * 云端密钥（2026-10-26 调整）：云端调用由**测试者自带 Key**。
 *  - 只存本标签页（sessionStorage），关闭标签页即消失；
 *  - 只随请求头发给后端，不进 URL、不进日志、不落盘；
 *  - 界面永远只显示掩码；测试结束请点「删除密钥」。
 */
export function LlmPanel() {
  const { llmNarrative, llmStatus, useLlm, setUseLlm, llmPreferred,
          setLlmPreferred, streaming,
          testerKey, testerModel, testerProvider, providers,
          loadProviders, setTesterProvider,
          keyCheck, applyTesterKey, clearTesterKey } = useStore()

  useEffect(() => {
    void loadProviders()
  }, [loadProviders])

  const currentProvider = useMemo(
    () => providers.find((p) => p.id === testerProvider) ?? providers[0],
    [providers, testerProvider],
  )

  const [open, setOpen] = useState(false)
  const [keyInput, setKeyInput] = useState('')
  const [modelInput, setModelInput] = useState(testerModel)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const online = llmStatus?.available ?? false
  const model = llmNarrative?.model || llmStatus?.model || '未接入'
  const cloud = llmStatus?.cloud
  // 当前实际会走哪一级：云优先且云端可用时才是"云端在线"
  const activeCloud = online && llmStatus?.active_hint === 'cloud'
  const hasKey = testerKey.trim().length > 0
  const envConfigured = llmStatus?.cloud?.key_source === 'env'

  const PROVIDERS: { value: 'auto' | 'local' | 'cloud'; label: string }[] = [
    { value: 'auto', label: '自动（云端优先）' },
    { value: 'local', label: '本地 Ollama' },
    { value: 'cloud', label: '云端 DeepSeek' },
  ]

  async function doVerify() {
    setBusy(true)
    const r = await applyTesterKey(keyInput, testerProvider, modelInput)
    setMsg({ ok: r.ok, text: r.message })
    if (r.ok) {
      setKeyInput('')
      setOpen(false)
    }
    setBusy(false)
  }

  function doClear() {
    clearTesterKey()
    setMsg({ ok: true, text: '已从本标签页删除密钥。关掉标签页也不会再留下任何副本。' })
  }

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
            {llmStatus
              ? online
                ? activeCloud
                  ? '云端在线'
                  : '本地在线'
                : `离线：${llmStatus.reason}`
              : '状态未知'}
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
            <span className="font-mono">
              {llmStatus?.cloud_provider_label || cloud.provider}
            </span>
            <span className="mx-1.5">·</span>
            <span className="font-mono">{cloud.model}</span>
            <span className="mx-1.5">·</span>
            {cloud.configured
              ? cloud.available
                ? '云端在线'
                : `云端不可达：${cloud.reason}`
              : '云端未配置（没有密钥，自动走本地/规则层）'}
          </div>
        )}

        {/* ---- 我的云端密钥（测试者自带） ---- */}
        <div
          className={`rounded border px-2 py-1.5 ${
            hasKey ? 'border-amber-300 bg-amber-50/60' : 'border-ink-200'
          }`}
        >
          <div className="flex flex-wrap items-center justify-between gap-1.5">
            <span className="text-[11px] text-ink-700">
              我的云端密钥
              <span className="ml-1.5 rounded bg-ink-100 px-1 text-[10px] text-ink-500">
                {hasKey ? '本标签页' : envConfigured ? '服务端环境变量' : '未填写'}
              </span>
              {hasKey && (
                <span className="ml-1.5 font-mono text-[10px] text-ink-500">
                  {keyCheck?.masked || '已保存'}
                </span>
              )}
            </span>
            <span className="flex items-center gap-1">
              <button
                disabled={busy}
                onClick={() => {
                  setMsg(null)
                  setOpen((v) => !v)
                }}
                className="rounded border border-ink-300 px-1.5 py-0.5 text-[10px] text-ink-600 hover:bg-white disabled:opacity-40"
              >
                {open ? '收起' : hasKey ? '更换密钥' : '填写我的密钥'}
              </button>
              {hasKey && (
                <button
                  disabled={busy}
                  onClick={doClear}
                  className="rounded bg-rose-600 px-1.5 py-0.5 text-[10px] font-medium text-white hover:bg-rose-700 disabled:opacity-40"
                  title="测试结束请点这里删除密钥"
                >
                  删除密钥
                </button>
              )}
            </span>
          </div>

          {hasKey && (
            <div className="mt-1 text-[10px] leading-4 text-amber-800">
              本轮调试使用的是<strong>你自己的</strong>密钥，仅保存在本标签页。
              测试结束后请点右侧「删除密钥」；直接关标签页也会清除。
            </div>
          )}

          {open && (
            <div className="mt-1.5 space-y-1.5 border-t border-ink-200 pt-1.5">
              <div className="text-[10px] leading-4 text-ink-500">
                用你自己的 DeepSeek（或其他 OpenAI 兼容服务）Key 体验云端解读。
                密钥只随本次请求发到后端内存、用完即丢：<strong>不写数据库、不写文件、不写日志</strong>；
                页面只显示掩码。测试结束请删除。
              </div>
              <div className="flex flex-wrap items-center gap-1.5">
                <select
                  className="min-w-[10rem] flex-1 rounded border border-ink-200 px-1.5 py-1 text-[11px] outline-none focus:border-ink-400"
                  value={testerProvider}
                  onChange={(e) => {
                    setTesterProvider(e.target.value)
                    setModelInput('')
                  }}
                >
                  {providers.length === 0 && (
                    <option value="deepseek">DeepSeek（深度求索）</option>
                  )}
                  {providers.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                    </option>
                  ))}
                </select>
                <input
                  list="xz-model-options"
                  className="min-w-[9rem] flex-1 rounded border border-ink-200 px-1.5 py-1 font-mono text-[11px] outline-none focus:border-ink-400"
                  placeholder={`模型（留空用 ${currentProvider?.default_model ?? '默认'}）`}
                  value={modelInput}
                  maxLength={80}
                  onChange={(e) => setModelInput(e.target.value)}
                />
                <datalist id="xz-model-options">
                  {(currentProvider?.models ?? []).map((m) => (
                    <option key={m} value={m} />
                  ))}
                </datalist>
              </div>
              {currentProvider?.note && (
                <div className="text-[10px] leading-4 text-ink-400">
                  {currentProvider.note}
                </div>
              )}
              <input
                type="password"
                autoComplete="off"
                spellCheck={false}
                className="w-full rounded border border-ink-200 px-1.5 py-1 font-mono text-[11px] outline-none focus:border-ink-400"
                placeholder={`粘贴 ${currentProvider?.label ?? '服务商'} 的 API Key`}
                value={keyInput}
                maxLength={200}
                onChange={(e) => setKeyInput(e.target.value)}
              />
              <div className="flex items-center gap-1.5">
                <button
                  disabled={busy || keyInput.trim().length < 8}
                  onClick={() => void doVerify()}
                  className="rounded bg-ink-800 px-2 py-1 text-[11px] text-white hover:bg-ink-700 disabled:opacity-50"
                >
                  {busy ? '校验中…' : '保存并校验'}
                </button>
                <span className="text-[10px] text-ink-400">
                  校验只做一次鉴权探测，不触发计费
                </span>
              </div>
            </div>
          )}

          {msg && (
            <div
              className={`mt-1.5 rounded px-2 py-1 text-[10px] leading-4 ${
                msg.ok ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'
              }`}
            >
              {msg.text}
            </div>
          )}
        </div>

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
          模型只做解释与补充建议，不参与候选根因判定与评分；
          输出先过证据纪律守卫（禁止声称实测/已验证、禁止标注证据等级），
          未通过即整段作废。
        </div>
      </div>
    </Card>
  )
}
