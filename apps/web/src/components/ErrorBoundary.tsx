import { Component, type ReactNode } from 'react'

/**
 * 全局错误边界：捕获渲染期异常，阻止整页白屏。
 *
 * 本项目诊断结果/报告页存在多来源数据（SSE 流式事件、DB 快照、
 * 模型解读、Markdown 渲染），字段形态曾出现过前后端契约不一致
 * （如 matched_terms 数组/字符串双形态），任一渲染异常都会让
 * React 卸载整棵组件树。此处兜底：显示可恢复的错误卡，并提供
 * 「刷新页面」与「返回初始状态」两种恢复路径，而不是白屏。
 */
interface Props {
  children: ReactNode
}
interface State {
  hasError: boolean
  message: string
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, message: '' }

  static getDerivedStateFromError(err: unknown): State {
    return {
      hasError: true,
      message: err instanceof Error ? err.message : String(err),
    }
  }

  componentDidCatch(err: unknown, info: unknown) {
    console.error('[ErrorBoundary] 渲染异常', err, info)
  }

  handleReset = () => {
    // 回到初始状态：清掉渲染异常的会话内状态，重新挂载 App。
    this.setState({ hasError: false, message: '' })
  }

  render() {
    if (this.state.hasError) {
      return (
        <div
          style={{
            minHeight: '100vh',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 16,
            padding: 24,
            background: '#0f172a',
            color: '#e2e8f0',
            fontFamily: 'ui-sans-serif, system-ui, sans-serif',
          }}
        >
          <h1
            style={{
              fontSize: 20,
              fontWeight: 600,
              color: '#f87171',
              margin: 0,
            }}
          >
            界面渲染出错
          </h1>
          <p style={{ fontSize: 13, color: '#94a3b8', maxWidth: 560, textAlign: 'center', margin: 0 }}>
            {this.state.message || '发生了未预期的渲染异常。'}
          </p>
          <p style={{ fontSize: 12, color: '#64748b', margin: 0 }}>
            诊断数据仍完整保存在后端，刷新或重置后可继续查看。
          </p>
          <div style={{ display: 'flex', gap: 12 }}>
            <button
              onClick={() => window.location.reload()}
              style={{
                padding: '8px 16px',
                borderRadius: 8,
                border: 'none',
                background: '#3b82f6',
                color: '#fff',
                fontSize: 14,
                cursor: 'pointer',
              }}
            >
              刷新页面
            </button>
            <button
              onClick={this.handleReset}
              style={{
                padding: '8px 16px',
                borderRadius: 8,
                border: '1px solid #475569',
                background: 'transparent',
                color: '#e2e8f0',
                fontSize: 14,
                cursor: 'pointer',
              }}
            >
              返回初始状态
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
