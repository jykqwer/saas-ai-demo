import { useState } from 'react'
import { ChatApiError, loginUser, registerUser } from '../chat/chatApi.js'


export default function AuthPage({ onAuthenticated }) {
  const [mode, setMode] = useState('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const submit = async (event) => {
    event.preventDefault()
    setSubmitting(true)
    setError('')
    setNotice('')
    try {
      if (mode === 'register') {
        const result = await registerUser(username.trim(), password)
        setNotice(result.message)
        setMode('login')
        setPassword('')
      } else {
        const result = await loginUser(username.trim(), password)
        onAuthenticated(result)
      }
    } catch (err) {
      setError(err instanceof ChatApiError ? err.message : '请求失败，请稍后重试。')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-card">
        <div className="auth-logo" aria-hidden="true">💬</div>
        <h1>云枢 CloudHub</h1>
        <p className="auth-subtitle">登录后使用 AI 客服演示</p>

        <div className="auth-tabs" role="tablist">
          <button
            type="button"
            className={mode === 'login' ? 'active' : ''}
            onClick={() => { setMode('login'); setError(''); setNotice('') }}
          >
            登录
          </button>
          <button
            type="button"
            className={mode === 'register' ? 'active' : ''}
            onClick={() => { setMode('register'); setError(''); setNotice('') }}
          >
            注册
          </button>
        </div>

        <form className="auth-form" onSubmit={submit}>
          <label>
            用户名
            <input
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              minLength={3}
              maxLength={32}
              pattern="[A-Za-z0-9_.-]+"
              placeholder="3–32 位字母、数字或 ._-"
              required
            />
          </label>
          <label>
            密码
            <input
              type="password"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              minLength={8}
              maxLength={128}
              placeholder="至少 8 位"
              required
            />
          </label>
          {mode === 'register' && (
            <p className="auth-note">注册后需等待 superuser 审批，审批前无法登录。</p>
          )}
          {error && <div className="form-error">⚠️ {error}</div>}
          {notice && <div className="success-banner auth-notice">✅ {notice}</div>}
          <button type="submit" className="btn btn-primary auth-submit" disabled={submitting}>
            {submitting ? '处理中…' : mode === 'login' ? '登录' : '提交注册申请'}
          </button>
        </form>
      </section>
    </main>
  )
}
