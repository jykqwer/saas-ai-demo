import { useState } from 'react'


const CONTACT_TYPES = [
  { value: 'wechat', label: '微信' },
  { value: 'phone', label: '手机号' },
  { value: 'email', label: '邮箱' },
]


export default function HandoffModal({ open, onSubmit, onClose, submitting }) {
  const [contactName, setContactName] = useState('')
  const [contactType, setContactType] = useState('wechat')
  const [contactValue, setContactValue] = useState('')
  const [subject, setSubject] = useState('')
  const [localError, setLocalError] = useState('')

  if (!open) return null

  const submit = (e) => {
    e.preventDefault()
    if (!contactName.trim() || !contactValue.trim()) {
      setLocalError('请填写称呼与联系方式')
      return
    }
    setLocalError('')
    onSubmit({
      contactName: contactName.trim(),
      contactType,
      contactValue: contactValue.trim(),
      subject: subject.trim(),
    })
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label="转人工客服"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h3>👋 转人工客服</h3>
          <button type="button" className="modal-close" aria-label="关闭" onClick={onClose}>
            ✕
          </button>
        </div>
        <p className="modal-desc">
          留下你的联系方式，专业顾问将在工作时间内尽快与你联系。
        </p>
        <form onSubmit={submit} className="modal-form">
          <div className="field">
            <label htmlFor="handoff-name">你的称呼</label>
            <input
              id="handoff-name"
              value={contactName}
              onChange={(e) => setContactName(e.target.value)}
              placeholder="例如：张先生"
              maxLength={60}
            />
          </div>
          <div className="field">
            <label>联系方式</label>
            <div className="contact-type-row">
              {CONTACT_TYPES.map((t) => (
                <button
                  key={t.value}
                  type="button"
                  className={`contact-type-btn ${contactType === t.value ? 'active' : ''}`}
                  onClick={() => setContactType(t.value)}
                >
                  {t.label}
                </button>
              ))}
            </div>
            <input
              value={contactValue}
              onChange={(e) => setContactValue(e.target.value)}
              placeholder={
                contactType === 'wechat'
                  ? '微信号'
                  : contactType === 'phone'
                    ? '手机号'
                    : '邮箱地址'
              }
              maxLength={120}
            />
          </div>
          <div className="field">
            <label htmlFor="handoff-subject">想咨询的内容（可选）</label>
            <input
              id="handoff-subject"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="例如：企业版报价、私有化部署方案"
              maxLength={200}
            />
          </div>
          {localError && <div className="form-error">⚠️ {localError}</div>}
          <div className="modal-actions">
            <button type="button" className="btn btn-ghost" onClick={onClose}>
              取消
            </button>
            <button type="submit" className="btn btn-primary" disabled={submitting}>
              {submitting ? '提交中…' : '提交转接申请'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
