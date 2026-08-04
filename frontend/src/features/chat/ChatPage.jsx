import { useCallback, useEffect, useRef, useState } from 'react'
import ChatMessage from './ChatMessage.jsx'
import QuickQuestions from './QuickQuestions.jsx'
import HandoffModal from './HandoffModal.jsx'
import KnowledgeModal from './KnowledgeModal.jsx'
import UserAdminModal from '../auth/UserAdminModal.jsx'
import {
  ChatApiError,
  deleteSession,
  fetchChatConfig,
  getSessionMessages,
  listSessions,
  sendHandoff,
  streamChat,
} from './chatApi.js'


export default function ChatPage({
  currentUser,
  quota,
  onQuotaChange,
  onLogout,
  onSessionExpired,
}) {
  const [config, setConfig] = useState(null)
  const [sessions, setSessions] = useState([])
  const [activeSessionId, setActiveSessionId] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [images, setImages] = useState([])
  const [streaming, setStreaming] = useState(false)
  const [error, setError] = useState('')
  const [mode, setMode] = useState('auto')
  const [handoffOpen, setHandoffOpen] = useState(false)
  const [handoffKey, setHandoffKey] = useState(1)
  const [handoffSubmitting, setHandoffSubmitting] = useState(false)
  const [handoffDone, setHandoffDone] = useState('')
  const [knowledgeOpen, setKnowledgeOpen] = useState(false)
  const [knowledgeKey, setKnowledgeKey] = useState(2)
  const [knowledgeDocTarget, setKnowledgeDocTarget] = useState(null)
  const [adminOpen, setAdminOpen] = useState(false)

  const activeSessionRef = useRef(null)
  const messagesRef = useRef(null)
  const inputRef = useRef(null)

  const openHandoff = useCallback(() => {
    // 递增 key 强制重挂载弹窗，达到“每次打开清空表单”的效果
    setHandoffKey((k) => k + 1)
    setHandoffOpen(true)
  }, [])

  const openKnowledge = useCallback(() => {
    setKnowledgeKey((k) => k + 1)
    setKnowledgeOpen(true)
  }, [])

  const openSourceDoc = useCallback((source) => {
    // 点击知识库来源 chip：打开管理弹窗并定位到该文档。
    setKnowledgeDocTarget(`${source}.md`)
    setKnowledgeKey((k) => k + 1)
    setKnowledgeOpen(true)
  }, [])

  const setSession = useCallback((id) => {
    activeSessionRef.current = id
    setActiveSessionId(id)
  }, [])

  // 加载引导配置与会话列表
  useEffect(() => {
    let cancelled = false
    fetchChatConfig()
      .then((data) => {
        if (!cancelled) setConfig(data)
      })
      .catch(() => {
        if (!cancelled) setError('无法连接 AI 服务，请确认后端已启动。')
      })
    listSessions()
      .then((data) => {
        if (!cancelled) setSessions(data ?? [])
      })
      .catch(() => {
        /* 会话加载失败不阻塞聊天 */
      })
    return () => {
      cancelled = true
    }
  }, [])

  // 新消息或流式增量后自动滚到底部
  useEffect(() => {
    const el = messagesRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, streaming])

  const refreshSessions = useCallback(async () => {
    try {
      const data = await listSessions()
      setSessions(data ?? [])
    } catch {
      /* 忽略刷新失败 */
    }
  }, [])

  const loadSession = useCallback(
    async (sessionId) => {
      try {
        const data = await getSessionMessages(sessionId)
        setMessages(
          (data.messages ?? []).map((m) => {
            const src = m.sources ?? null
            return {
              role: m.role,
              content: m.content,
              // 从持久化来源恢复展示（RAG/联网 chips 与链接）。
              ragSources: src?.rag?.length ? src.rag : undefined,
              webSearch: src?.web_query || undefined,
              webResults: src?.web?.length ? src.web : undefined,
              // 仅助手消息展示来源；用户消息不附加 meta。
              meta:
                m.role === 'assistant'
                  ? m.mock
                    ? '演示模式 · 配置 LLM_API_KEY 后接入真实大模型'
                    : `${m.provider ?? ''} · ${m.model ?? ''}`
                  : undefined,
            }
          }),
        )
        setSession(sessionId)
        setError('')
      } catch (err) {
        setError(err instanceof ChatApiError ? err.message : '加载会话失败。')
      }
    },
    [setSession],
  )

  // 出错或中断时，移除末尾空的助手占位气泡
  const removeEmptyPlaceholder = useCallback(() => {
    setMessages((prev) => {
      const last = prev[prev.length - 1]
      if (last?.role === 'assistant' && last.streaming && !last.content) {
        return prev.slice(0, -1)
      }
      return prev
    })
  }, [])

  const send = useCallback(
    async (rawText) => {
      const text = (rawText ?? input).trim()
      if (!text || streaming) return
      if (!quota?.unlimited && (quota?.remaining ?? 0) <= 0) {
        setError(`今日问答次数已用完（每天 ${quota?.limit ?? 10} 次），请明天再试。`)
        return
      }

      const sessionId = activeSessionRef.current
      const imagePayload = images.map((image) => image.url)
      setInput('')
      setImages([])
      setError('')
      setHandoffDone('')
      // 用户消息 + 单个助手占位气泡：打字动画/流式内容都在同一个气泡内，
      // 避免出现“两个回答框”，也避免结束后整表重拉导致的闪烁。
      setMessages((prev) => [
        ...prev,
        { role: 'user', content: text, images: imagePayload },
        { role: 'assistant', content: '', streaming: true },
      ])
      setStreaming(true)

      const result = await streamChat(
        { content: text, sessionId, mode, images: imagePayload },
        {
          onMeta: (meta) => {
            if (!activeSessionRef.current) setSession(meta.session_id)
            if (meta.quota) onQuotaChange?.(meta.quota)
            // 记录 RAG 命中的知识库来源。
            if (meta.rag?.length) {
              setMessages((prev) => {
                const next = [...prev]
                const last = next[next.length - 1]
                if (last?.role === 'assistant' && last.streaming) {
                  next[next.length - 1] = { ...last, ragSources: meta.rag }
                }
                return next
              })
            }
          },
          onDelta: (delta) => {
            // 只更新末尾的助手占位消息，保持组件身份不重建。
            setMessages((prev) => {
              const next = [...prev]
              const last = next[next.length - 1]
              if (last?.role === 'assistant' && last.streaming) {
                next[next.length - 1] = { ...last, content: last.content + delta }
              }
              return next
            })
          },
          onReset: () => {
            setMessages((prev) => {
              const next = [...prev]
              const last = next[next.length - 1]
              if (last?.role === 'assistant' && last.streaming) {
                next[next.length - 1] = { ...last, content: '' }
              }
              return next
            })
          },
          onDone: (done) => {
            if (done.quota) onQuotaChange?.(done.quota)
            setMessages((prev) => {
              const next = [...prev]
              const last = next[next.length - 1]
              if (last?.role === 'assistant') {
                next[next.length - 1] = {
                  ...last,
                  meta: done.mock
                    ? '演示模式 · 配置 LLM_API_KEY 后接入真实大模型'
                    : `${done.provider} · ${done.model}`,
                }
              }
              return next
            })
          },
          onSearch: (payload) => {
            // 记录联网查询的完整结果（用于展示来源链接）。
            setMessages((prev) => {
              const next = [...prev]
              const last = next[next.length - 1]
              if (last?.role === 'assistant' && last.streaming) {
                next[next.length - 1] = {
                  ...last,
                  webSearch: payload.query,
                  webResults: payload.results ?? [],
                }
              }
              return next
            })
          },
          onRagUsed: (payload) => {
            // 记录本轮实际采用的知识库来源（模型调用检索工具后返回）。
            setMessages((prev) => {
              const next = [...prev]
              const last = next[next.length - 1]
              if (last?.role === 'assistant' && last.streaming) {
                next[next.length - 1] = { ...last, ragSources: payload.rag ?? [] }
              }
              return next
            })
          },
          onError: (payload) => {
            setError(
              payload.code === 'LLM_UPSTREAM_ERROR' ||
                payload.code === 'LLM_TIMEOUT'
                ? 'AI 服务暂时不可用，请稍后再试。'
                : payload.message || '出错了，请重试。',
            )
            removeEmptyPlaceholder()
          },
        },
      )

      // 兜底结束流式状态；网络中断未收到任何事件时移除空占位。
      if (!result.ok) {
        setError(result.error)
        removeEmptyPlaceholder()
        if (result.status === 401) onSessionExpired?.()
        if (result.code === 'DAILY_QUOTA_EXCEEDED') {
          onQuotaChange?.({ ...quota, used: quota?.limit ?? 10, remaining: 0 })
        }
      }
      setStreaming(false)
      setMessages((prev) => {
        const last = prev[prev.length - 1]
        if (last?.role === 'assistant' && last.streaming) {
          const next = [...prev]
          next[next.length - 1] = { ...last, streaming: false }
          return next
        }
        return prev
      })
      // 消息已随流式逐条维护在内存（与后端一致），只需刷新会话列表。
      refreshSessions()
    },
    [
      input,
      images,
      mode,
      onQuotaChange,
      onSessionExpired,
      quota,
      refreshSessions,
      removeEmptyPlaceholder,
      setSession,
      streaming,
    ],
  )

  const startNewConversation = useCallback(() => {
    setSession(null)
    setMessages([])
    setInput('')
    setImages([])
    setError('')
    setHandoffDone('')
    inputRef.current?.focus()
  }, [setSession])

  const removeSession = useCallback(
    async (sessionId, e) => {
      e.stopPropagation()
      try {
        await deleteSession(sessionId)
      } catch {
        /* 忽略 */
      }
      if (activeSessionRef.current === sessionId) startNewConversation()
      refreshSessions()
    },
    [refreshSessions, startNewConversation],
  )

  const submitHandoff = useCallback(
    async (payload) => {
      setHandoffSubmitting(true)
      try {
        await sendHandoff({
          sessionId: activeSessionRef.current,
          ...payload,
        })
        setHandoffOpen(false)
        setHandoffDone('已提交转接申请，客服将在工作时间内联系你 ✅')
      } catch (err) {
        setError(err instanceof ChatApiError ? err.message : '提交失败，请重试。')
      } finally {
        setHandoffSubmitting(false)
      }
    },
    [],
  )

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  const selectImages = (event) => {
    const files = Array.from(event.target.files ?? [])
    event.target.value = ''
    if (!config?.vision_configured) {
      setError('图片理解未配置，请设置 QWEN_API_KEY。')
      return
    }
    const accepted = files.filter(
      (file) => ['image/png', 'image/jpeg', 'image/webp'].includes(file.type) && file.size <= 5 * 1024 * 1024,
    )
    if (accepted.length !== files.length) {
      setError('仅支持 5MB 以内的 PNG、JPEG 或 WebP 图片。')
    }
    accepted.slice(0, Math.max(0, 4 - images.length)).forEach((file) => {
      const reader = new FileReader()
      reader.onload = () => {
        setImages((current) => [
          ...current,
          { name: file.name, url: String(reader.result) },
        ].slice(0, 4))
      }
      reader.readAsDataURL(file)
    })
  }

  const configured = config?.configured ?? false
  const hasMessages = messages.length > 0
  const isSuperuser = currentUser?.role === 'superuser'
  const canAsk = quota?.unlimited || (quota?.remaining ?? 0) > 0

  return (
    <div className="app-shell">
      {/* 侧边栏 */}
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-logo" aria-hidden="true">
            💬
          </div>
          <div>
            <div className="brand-name">
              {config?.assistant_name ?? '小枢'}
            </div>
            <div className="brand-sub">{config?.product_name ?? 'SaaS AI 助手'}</div>
          </div>
        </div>

        <div className="status-card">
          <div className="status-row">
            <span className={`status-dot ${configured ? 'online' : 'mock'}`} />
            <span>
              {configured ? (
                <>
                  已接入大模型 · <b>{config.provider}</b>
                </>
              ) : (
                <>
                  <b>演示模式</b> · 未配置 API Key
                </>
              )}
            </span>
          </div>
          <div className="status-row">
            <span>模型：{config?.model ?? '—'}</span>
          </div>
          {config?.rag_docs > 0 && (
            <div className="status-row">
              <span>📚 RAG 知识库：{config.rag_docs} 篇文档</span>
            </div>
          )}
          <div className="status-row user-summary">
            <span>👤 {currentUser.username}</span>
            <b>{isSuperuser ? 'superuser' : 'user'}</b>
          </div>
          <div className="status-row">
            <span>
              {quota?.unlimited
                ? '今日问答：不限次数'
                : `今日剩余：${quota?.remaining ?? 0} / ${quota?.limit ?? 10}`}
            </span>
          </div>
        </div>

        <button type="button" className="sidebar-link" onClick={startNewConversation}>
          ✨ 新对话
        </button>

        <div className="session-label">会话历史</div>
        <div className="session-list">
          {sessions.length === 0 && (
            <div className="session-empty">暂无历史会话</div>
          )}
          {sessions.map((s) => (
            <div
              key={s.id}
              className={`session-item ${activeSessionId === s.id ? 'active' : ''}`}
              onClick={() => loadSession(s.id)}
            >
              <span className="session-title">
                {s.title}
                {s.message_count > 0 && (
                  <span className="session-count">{s.message_count}</span>
                )}
              </span>
              <button
                type="button"
                className="session-del"
                aria-label="删除会话"
                onClick={(e) => removeSession(s.id, e)}
              >
                🗑
              </button>
            </div>
          ))}
        </div>

        <div className="sidebar-spacer" />

        {isSuperuser && (
          <>
            <button
              type="button"
              className="sidebar-link handoff-link"
              onClick={() => setAdminOpen(true)}
            >
              👥 用户审批
            </button>
            <button
              type="button"
              className="sidebar-link handoff-link"
              onClick={openKnowledge}
            >
              📚 知识库管理
            </button>
          </>
        )}
        <button
          type="button"
          className="sidebar-link handoff-link"
          onClick={openHandoff}
        >
          👋 转人工客服
        </button>
        <div className="sidebar-footer">
          <p>
            {config?.company_name ?? ''} AI 客服助手
            <br />
            服务时间：全天候 · 人工 9:00-18:00
          </p>
        </div>
      </aside>

      {/* 主聊天区 */}
      <main className="chat-main">
        <header className="chat-header">
          <div>
            <h1>
              {config?.assistant_name ?? '小枢'} · {config?.product_name ?? 'SaaS AI 助手'}
            </h1>
            <div className="hint">
              {configured ? '由真实大模型驱动' : '演示模式，配置 LLM_API_KEY 后接入真实大模型'}
            </div>
          </div>
          <div className="header-actions">
            <div className="mode-switch" role="group" aria-label="查询模式">
              <button
                type="button"
                className={`mode-btn ${mode === 'auto' ? 'active' : ''}`}
                onClick={() => setMode('auto')}
                title="知识库 + 按需联网"
              >
                智能
              </button>
              <button
                type="button"
                className={`mode-btn ${mode === 'web' ? 'active' : ''}`}
                onClick={() => setMode('web')}
                title="每次都联网搜索"
              >
                🌐 始终联网
              </button>
              <button
                type="button"
                className={`mode-btn ${mode === 'knowledge' ? 'active' : ''}`}
                onClick={() => setMode('knowledge')}
                title="只使用内部知识库，不联网"
              >
                📚 仅知识库
              </button>
            </div>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={openHandoff}
            >
              👋 转人工
            </button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={onLogout}>
              退出
            </button>
          </div>
        </header>

        <div className="chat-messages" ref={messagesRef}>
          {!hasMessages && !streaming && (
            <div className="welcome-card">
              <div className="welcome-avatar" aria-hidden="true">
                💬
              </div>
              <h2>你好，我是 {config?.assistant_name ?? '小枢'}</h2>
              <p>
                {config?.greeting ??
                  '我可以帮你了解产品、价格、试用与部署，也可以处理售后问题。'}
              </p>
              <QuickQuestions
                questions={config?.quick_questions}
                onPick={send}
                disabled={streaming || !canAsk}
              />
            </div>
          )}

          {messages.map((m, index) => (
            <ChatMessage key={index} message={m} onOpenSource={openSourceDoc} />
          ))}
        </div>

        {error && <div className="error-banner">⚠️ {error}</div>}
        {handoffDone && <div className="success-banner">✅ {handoffDone}</div>}

        <div className="chat-input-bar">
          {images.length > 0 && (
            <div className="image-preview-list">
              {images.map((image, index) => (
                <div className="image-preview" key={`${image.name}-${index}`}>
                  <img src={image.url} alt={image.name} />
                  <button type="button" onClick={() => setImages((current) => current.filter((_, i) => i !== index))}>×</button>
                </div>
              ))}
            </div>
          )}
          <div className="chat-input-wrap">
            <label className={`image-upload-btn ${config?.vision_configured ? '' : 'disabled'}`} title="添加图片">
              🖼
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                multiple
                disabled={streaming || !canAsk || !config?.vision_configured}
                onChange={selectImages}
              />
            </label>
            <textarea
              ref={inputRef}
              rows={1}
              placeholder={canAsk ? '输入你的问题，按 Enter 发送，Shift+Enter 换行……' : '今日问答次数已用完'}
              value={input}
              disabled={streaming || !canAsk}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              onInput={(e) => {
                e.target.style.height = 'auto'
                e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`
              }}
            />
            <button
              type="button"
              className="send-btn"
              aria-label="发送"
              disabled={streaming || !canAsk || !input.trim()}
              onClick={() => send()}
            >
              ➤
            </button>
          </div>
          <div className="input-hint">
            {quota?.unlimited
              ? 'superuser 不限调用次数'
              : `普通用户每天可问答 ${quota?.limit ?? 10} 次 · 今日剩余 ${quota?.remaining ?? 0} 次`}
            {' · AI 可能犯错，请核对重要信息'}
          </div>
        </div>
      </main>

      <HandoffModal
        key={handoffKey}
        open={handoffOpen}
        submitting={handoffSubmitting}
        onSubmit={submitHandoff}
        onClose={() => setHandoffOpen(false)}
      />

      <KnowledgeModal
        key={knowledgeKey}
        open={knowledgeOpen}
        initialDoc={knowledgeDocTarget}
        readOnly={!isSuperuser}
        onClose={() => {
          setKnowledgeOpen(false)
          setKnowledgeDocTarget(null)
        }}
        onChanged={() => {
          // 文档变化后刷新配置，让状态卡里的 RAG 计数保持最新
          fetchChatConfig()
            .then(setConfig)
            .catch(() => {})
        }}
      />

      <UserAdminModal open={adminOpen} onClose={() => setAdminOpen(false)} />
    </div>
  )
}
