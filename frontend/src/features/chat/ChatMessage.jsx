import { useMemo, useState } from 'react'

const DEFAULT_VISIBLE_WEB_SOURCES = 3

/**
 * 轻量消息渲染：把 **加粗** 语法转成 <strong>，保留换行。
 * 不引入 Markdown 依赖，保持零额外包。
 */
export default function ChatMessage({ message, onOpenSource }) {
  const [expandedWebSourceIdentity, setExpandedWebSourceIdentity] = useState('')
  const isUser = message.role === 'user'
  const isTyping = message.streaming && !message.content
  // 只展示“本轮实际采用的来源”：ragSources 由模型调用检索工具后写入，
  // 不再是无条件预检索的候选，因此可与联网来源并列展示。
  const showRagSources = message.ragSources?.length > 0
  const showWebSources = message.webResults?.length > 0
  const webCountLabel = showWebSources ? `（${message.webResults.length} 条结果）` : ''
  const webSourceIdentity = useMemo(
    () => (message.webResults ?? []).map((result) => result.url).join('\n'),
    [message.webResults],
  )

  const webSourcesExpanded = expandedWebSourceIdentity === webSourceIdentity

  const visibleWebResults = webSourcesExpanded
    ? message.webResults
    : message.webResults?.slice(0, DEFAULT_VISIBLE_WEB_SOURCES)
  const hiddenWebSourceCount = Math.max(
    0,
    (message.webResults?.length ?? 0) - DEFAULT_VISIBLE_WEB_SOURCES,
  )

  const renderBold = (text) => {
    const parts = text.split(/(\*\*[^*]+\*\*)/g)
    return parts.map((part, index) => {
      if (part.startsWith('**') && part.endsWith('**')) {
        return <strong key={index}>{part.slice(2, -2)}</strong>
      }
      return part
    })
  }

  return (
    <div className={`msg ${isUser ? 'user' : 'assistant'}`}>
      <div className="msg-avatar" aria-hidden="true">
        {isUser ? '我' : '枢'}
      </div>
      <div className="msg-body">
        {message.webSearch && !isUser && (
          <div className="web-badge">
            🌐 已联网查询「{message.webSearch}」{webCountLabel}
          </div>
        )}
        <div className={`msg-bubble ${message.streaming ? 'streaming' : ''}`}>
          {isTyping ? (
            <span className="typing" aria-label="正在输入">
              <span />
              <span />
              <span />
            </span>
          ) : (
            renderBold(message.content)
          )}
        </div>
        {!isUser && (showRagSources || showWebSources) && (
          <div className="sources">
            {showRagSources && (
              <div className="sources-group">
                <div className="sources-title">📚 知识库参考</div>
                <div className="sources-chips">
                  {message.ragSources.map((s) => (
                    <button
                      type="button"
                      className="source-chip"
                      key={`${s.source}-${s.heading}`}
                      onClick={() => onOpenSource?.(s.source)}
                      title="在知识库中查看该文档"
                    >
                      {s.source}/{s.heading}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {showWebSources && (
              <div className="sources-group">
                <div className="sources-title">🌐 联网来源</div>
                <div className="sources-links">
                  {visibleWebResults.map((r, i) => (
                    <a
                      className="source-link"
                      key={`${r.url}-${i}`}
                      href={r.url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      <span className="source-link-title">{r.title || r.url}</span>
                      {r.snippet && (
                        <span className="source-link-snippet">{r.snippet}</span>
                      )}
                    </a>
                  ))}
                </div>
                {hiddenWebSourceCount > 0 && (
                  <button
                    type="button"
                    className="sources-toggle"
                    aria-expanded={webSourcesExpanded}
                    onClick={() =>
                      setExpandedWebSourceIdentity((identity) =>
                        identity === webSourceIdentity ? '' : webSourceIdentity,
                      )
                    }
                  >
                    {webSourcesExpanded
                      ? `收起至前 ${DEFAULT_VISIBLE_WEB_SOURCES} 个来源`
                      : `查看其余 ${hiddenWebSourceCount} 个来源`}
                  </button>
                )}
              </div>
            )}
          </div>
        )}
        {message.meta && <div className="msg-meta">{message.meta}</div>}
      </div>
    </div>
  )
}
