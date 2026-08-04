/**
 * 与后端 AI 助手接口的轻量客户端。
 * 所有请求都走 Vite 的 /api 代理，因此浏览器无需知道后端地址。
 */

export class ChatApiError extends Error {
  constructor(message, { status = 0, code = 'NETWORK_ERROR' } = {}) {
    super(message)
    this.name = 'ChatApiError'
    this.status = status
    this.code = code
  }
}

const TOKEN_KEY = 'cloudhub_access_token'

export function getAccessToken() {
  return window.localStorage.getItem(TOKEN_KEY) ?? ''
}

export function setAccessToken(token) {
  if (token) window.localStorage.setItem(TOKEN_KEY, token)
  else window.localStorage.removeItem(TOKEN_KEY)
}

function authorizedHeaders(headers = {}) {
  const token = getAccessToken()
  return {
    ...headers,
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  }
}

async function parseResponse(response) {
  let body
  try {
    body = await response.json()
  } catch {
    body = null
  }

  if (!response.ok) {
    const message =
      body?.message ??
      (response.status === 502
        ? 'AI 服务暂时不可用，请稍后再试'
        : '请求没有完成')
    throw new ChatApiError(message, {
      status: response.status,
      code: body?.code ?? 'HTTP_ERROR',
    })
  }
  return body
}

export async function fetchChatConfig() {
  const response = await fetch('/api/v1/chat/config', {
    headers: { Accept: 'application/json' },
  })
  return parseResponse(response)
}

export async function sendChatMessage({ content, sessionId }) {
  const response = await fetch('/api/v1/chat', {
    method: 'POST',
    headers: authorizedHeaders({
      'Content-Type': 'application/json',
      Accept: 'application/json',
    }),
    body: JSON.stringify({ content, session_id: sessionId ?? null }),
  })
  return parseResponse(response)
}

/**
 * SSE 流式聊天。解析后端推送的 data 帧，按类型回调：
 * - meta:  { type, session_id, model, provider, mock }
 * - delta: { type, text }
 * - reset: 清除工具调用轮产生的临时可见文本
 * - done:  { type, session_id, ... }
 * - error: { type, code, message }
 * 返回 { ok, error }。
 */
export async function streamChat({ content, sessionId, mode, images = [] }, handlers) {
  const response = await fetch('/api/v1/chat/stream', {
    method: 'POST',
    headers: authorizedHeaders({
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    }),
    body: JSON.stringify({
      content,
      session_id: sessionId ?? null,
      mode: mode ?? 'auto',
      images,
    }),
  })

  if (!response.ok) {
    let body = null
    try {
      body = await response.json()
    } catch {
      /* ignore */
    }
    return {
      ok: false,
      error: body?.message ?? `请求失败（HTTP ${response.status}）`,
      code: body?.code ?? 'HTTP_ERROR',
      status: response.status,
    }
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const handleEvent = (payload) => {
    if (payload?.type === 'meta') handlers.onMeta?.(payload)
    else if (payload?.type === 'delta') handlers.onDelta?.(payload.text ?? '')
    else if (payload?.type === 'search') handlers.onSearch?.(payload)
    else if (payload?.type === 'rag_used') handlers.onRagUsed?.(payload)
    else if (payload?.type === 'reset') handlers.onReset?.()
    else if (payload?.type === 'done') handlers.onDone?.(payload)
    else if (payload?.type === 'error') handlers.onError?.(payload)
  }

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      // 按空行切分 SSE 事件；保留可能截断的尾部。
      const frames = buffer.split('\n\n')
      buffer = frames.pop() ?? ''
      for (const frame of frames) {
        const line = frame
          .split('\n')
          .find((l) => l.startsWith('data: '))
        if (!line) continue
        try {
          handleEvent(JSON.parse(line.slice(6)))
        } catch {
          // 忽略无法解析的事件
        }
      }
    }
    // 处理结尾残留帧
    if (buffer.trim()) {
      const line = buffer.split('\n').find((l) => l.startsWith('data: '))
      if (line) {
        try {
          handleEvent(JSON.parse(line.slice(6)))
        } catch {
          /* ignore */
        }
      }
    }
  } catch {
    return { ok: false, error: '连接中断，请重试。' }
  }
  return { ok: true }
}

export async function listSessions() {
  const response = await fetch('/api/v1/sessions', {
    headers: authorizedHeaders({ Accept: 'application/json' }),
  })
  return parseResponse(response)
}

export async function getSessionMessages(sessionId) {
  const response = await fetch(`/api/v1/sessions/${sessionId}`, {
    headers: authorizedHeaders({ Accept: 'application/json' }),
  })
  return parseResponse(response)
}

export async function deleteSession(sessionId) {
  const response = await fetch(`/api/v1/sessions/${sessionId}`, {
    method: 'DELETE',
    headers: authorizedHeaders({ Accept: 'application/json' }),
  })
  return parseResponse(response)
}

export async function sendHandoff({
  sessionId,
  contactName,
  contactType,
  contactValue,
  subject,
}) {
  const response = await fetch('/api/v1/chat/handoff', {
    method: 'POST',
    headers: authorizedHeaders({
      'Content-Type': 'application/json',
      Accept: 'application/json',
    }),
    body: JSON.stringify({
      session_id: sessionId ?? null,
      contact_name: contactName,
      contact_type: contactType,
      contact_value: contactValue,
      subject,
    }),
  })
  return parseResponse(response)
}

// ============ 知识库管理 ============

export async function listKnowledgeDocs() {
  const response = await fetch('/api/v1/knowledge/docs', {
    headers: authorizedHeaders({ Accept: 'application/json' }),
  })
  return parseResponse(response)
}

export async function getKnowledgeDoc(name) {
  const response = await fetch(`/api/v1/knowledge/docs/${encodeURIComponent(name)}`, {
    headers: authorizedHeaders({ Accept: 'application/json' }),
  })
  return parseResponse(response)
}

export async function importKnowledgeDoc(filename, content) {
  const response = await fetch('/api/v1/knowledge/docs', {
    method: 'POST',
    headers: authorizedHeaders({
      'Content-Type': 'application/json',
      Accept: 'application/json',
    }),
    body: JSON.stringify({ filename, content }),
  })
  return parseResponse(response)
}

export async function deleteKnowledgeDoc(name) {
  const response = await fetch(`/api/v1/knowledge/docs/${encodeURIComponent(name)}`, {
    method: 'DELETE',
    headers: authorizedHeaders({ Accept: 'application/json' }),
  })
  return parseResponse(response)
}

export async function searchKnowledge({ query, doc, k = 5 }) {
  const response = await fetch('/api/v1/knowledge/retrieve', {
    method: 'POST',
    headers: authorizedHeaders({
      'Content-Type': 'application/json',
      Accept: 'application/json',
    }),
    body: JSON.stringify({ query, doc: doc ?? null, k }),
  })
  return parseResponse(response)
}

// ============ 用户认证与审批 ============

export async function registerUser(username, password) {
  const response = await fetch('/api/v1/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  return parseResponse(response)
}

export async function loginUser(username, password) {
  const response = await fetch('/api/v1/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  const data = await parseResponse(response)
  setAccessToken(data.access_token)
  return data
}

export async function fetchCurrentUser() {
  const response = await fetch('/api/v1/auth/me', {
    headers: authorizedHeaders({ Accept: 'application/json' }),
  })
  return parseResponse(response)
}

export async function logoutUser() {
  try {
    const response = await fetch('/api/v1/auth/logout', {
      method: 'POST',
      headers: authorizedHeaders({ Accept: 'application/json' }),
    })
    await parseResponse(response)
  } finally {
    setAccessToken('')
  }
}

export async function listUsers(status = '') {
  const query = status ? `?status_filter=${encodeURIComponent(status)}` : ''
  const response = await fetch(`/api/v1/admin/users${query}`, {
    headers: authorizedHeaders({ Accept: 'application/json' }),
  })
  return parseResponse(response)
}

export async function reviewUser(userId, action) {
  const response = await fetch(`/api/v1/admin/users/${userId}/${action}`, {
    method: 'POST',
    headers: authorizedHeaders({ Accept: 'application/json' }),
  })
  return parseResponse(response)
}
