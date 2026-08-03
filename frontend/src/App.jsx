import { useEffect, useState } from 'react'
import AuthPage from './features/auth/AuthPage.jsx'
import ChatPage from './features/chat/ChatPage.jsx'
import {
  fetchCurrentUser,
  getAccessToken,
  logoutUser,
  setAccessToken,
} from './features/chat/chatApi.js'
import './App.css'


function App() {
  const [auth, setAuth] = useState(null)
  const [loading, setLoading] = useState(Boolean(getAccessToken()))

  useEffect(() => {
    if (!getAccessToken()) return
    let cancelled = false
    fetchCurrentUser()
      .then((data) => {
        if (!cancelled) setAuth(data)
      })
      .catch(() => {
        setAccessToken('')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [])

  const logout = async () => {
    await logoutUser()
    setAuth(null)
  }

  if (loading) return <div className="app-loading">正在验证登录状态…</div>
  if (!auth) return <AuthPage onAuthenticated={setAuth} />
  return (
    <ChatPage
      currentUser={auth.user}
      quota={auth.quota}
      onQuotaChange={(quota) => setAuth((prev) => ({ ...prev, quota }))}
      onLogout={logout}
      onSessionExpired={() => { setAccessToken(''); setAuth(null) }}
    />
  )
}


export default App
