import { useCallback, useEffect, useState } from 'react'
import { ChatApiError, listUsers, reviewUser } from '../chat/chatApi.js'


const STATUS_TEXT = {
  pending: '待审批',
  approved: '已通过',
  rejected: '已拒绝',
}


export default function UserAdminModal({ open, onClose }) {
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reviewing, setReviewing] = useState('')

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setUsers(await listUsers())
      setError('')
    } catch (err) {
      setError(err instanceof ChatApiError ? err.message : '加载用户失败。')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!open) return
    let cancelled = false
    listUsers()
      .then((data) => {
        if (!cancelled) {
          setUsers(data)
          setError('')
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ChatApiError ? err.message : '加载用户失败。')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [open])

  if (!open) return null

  const review = async (userId, action) => {
    setReviewing(userId)
    try {
      await reviewUser(userId, action)
      await refresh()
    } catch (err) {
      setError(err instanceof ChatApiError ? err.message : '审批失败。')
    } finally {
      setReviewing('')
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal modal-wide" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>👥 用户审批</h3>
          <button type="button" className="modal-close" onClick={onClose}>✕</button>
        </div>
        <p className="modal-desc">普通用户注册后必须由 superuser 审批才能登录。</p>
        {error && <div className="form-error">⚠️ {error}</div>}
        <div className="user-admin-list">
          {loading && <div className="kb-empty">加载中…</div>}
          {!loading && users.length === 0 && <div className="kb-empty">暂无用户</div>}
          {users.map((user) => (
            <div className="user-admin-row" key={user.id}>
              <div>
                <div className="user-admin-name">{user.username}</div>
                <div className="user-admin-meta">
                  {user.role} · <span className={`user-status ${user.status}`}>{STATUS_TEXT[user.status]}</span>
                </div>
              </div>
              {user.role === 'user' && (
                <div className="user-admin-actions">
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    disabled={reviewing === user.id || user.status === 'approved'}
                    onClick={() => review(user.id, 'approve')}
                  >通过</button>
                  <button
                    type="button"
                    className="btn btn-danger btn-sm"
                    disabled={reviewing === user.id || user.status === 'rejected'}
                    onClick={() => review(user.id, 'reject')}
                  >拒绝</button>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
