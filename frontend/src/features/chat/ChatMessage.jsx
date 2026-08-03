/**
 * 轻量消息渲染：把 **加粗** 语法转成 <strong>，保留换行。
 * 不引入 Markdown 依赖，保持零额外包。
 */
export default function ChatMessage({ message, onOpenSource }) {
  const isUser = message.role === 'user'
  const isTyping = message.streaming && !message.content
  // 联网回答时，知识库参考多为无关噪音，隐藏；只展示网络来源。
  const showRagSources = !message.webSearch && message.ragSources?.length > 0
  const showWebSources = message.webResults?.length > 0

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
            🌐 已联网查询「{message.webSearch}」{showWebSources ? `（${message.webResults.length} 条结果）` : ''}
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
                  {message.webResults.map((r, i) => (
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
              </div>
            )}
          </div>
        )}
        {message.meta && <div className="msg-meta">{message.meta}</div>}
      </div>
    </div>
  )
}
