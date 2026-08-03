import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ChatApiError,
  deleteKnowledgeDoc,
  getKnowledgeDoc,
  importKnowledgeDoc,
  listKnowledgeDocs,
  searchKnowledge,
} from './chatApi.js'


// 与后端一致的中文感知分词：英文/数字按单词，中文按二元组。
function tokenize(text) {
  const tokens = []
  for (const m of text.matchAll(/[A-Za-z0-9][A-Za-z0-9_.-]*/g)) {
    tokens.push(m[0].toLowerCase())
  }
  for (const m of text.matchAll(/[\u4e00-\u9fff]+/g)) {
    const run = m[0]
    if (run.length === 1) {
      tokens.push(run)
    } else {
      for (let i = 0; i < run.length - 1; i += 1) tokens.push(run.slice(i, i + 2))
      tokens.push(run[run.length - 1])
    }
  }
  return tokens
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function HighlightContent({ text, query }) {
  const terms = [...new Set(tokenize(query).filter((t) => t.length > 1))]
  if (!terms.length) return text
  const pattern = new RegExp(`(${terms.map(escapeRegExp).join('|')})`, 'gi')
  const parts = text.split(pattern)
  const set = new Set(terms)
  return parts.map((part, i) =>
    set.has(part.toLowerCase()) ? <mark key={i}>{part}</mark> : part,
  )
}


export default function KnowledgeModal({ open, initialDoc, onClose, onChanged }) {
  const [docs, setDocs] = useState([])
  // 初始即“加载中”；所有 setState 都发生在异步回调里，避免触发 set-state-in-effect。
  const [loading, setLoading] = useState(true)
  const [detail, setDetail] = useState(null)
  const [filename, setFilename] = useState('')
  const [content, setContent] = useState('')
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  // 检索测试页签
  const [tab, setTab] = useState('docs')
  const [query, setQuery] = useState('')
  const [docFilter, setDocFilter] = useState('')
  const [results, setResults] = useState([])
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState('')

  const fileRef = useRef(null)

  // 导入/删除后刷新列表（事件回调里可安全 setState）
  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const data = await listKnowledgeDocs()
      setDocs(data?.docs ?? [])
      setError('')
    } catch (err) {
      setError(err instanceof ChatApiError ? err.message : '加载知识库失败。')
    } finally {
      setLoading(false)
    }
  }, [])

  // 打开弹窗时加载文档列表；若指定 initialDoc 则自动打开对应文档。
  useEffect(() => {
    if (!open) return
    let cancelled = false
    listKnowledgeDocs()
      .then((data) => {
        if (cancelled) return
        const docs = data?.docs ?? []
        setDocs(docs)
        if (initialDoc && docs.some((d) => d.name === initialDoc)) {
          return getKnowledgeDoc(initialDoc).then((detail) => {
            if (!cancelled) setDetail(detail)
          })
        }
        return undefined
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ChatApiError ? err.message : '加载知识库失败。')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open, initialDoc])

  if (!open) return null

  const openDoc = async (name) => {
    setError('')
    try {
      const data = await getKnowledgeDoc(name)
      setDetail(data)
    } catch (err) {
      setError(err instanceof ChatApiError ? err.message : '读取文档失败。')
    }
  }

  const removeDoc = async (name) => {
    if (!window.confirm(`确定删除「${name}」吗？删除后检索将不再使用该文档。`)) {
      return
    }
    setError('')
    try {
      await deleteKnowledgeDoc(name)
      onChanged?.()
      refresh()
    } catch (err) {
      setError(err instanceof ChatApiError ? err.message : '删除失败。')
    }
  }

  const onFilePicked = (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      setFilename(file.name)
      setContent(String(reader.result ?? ''))
    }
    reader.readAsText(file)
    e.target.value = ''
  }

  const submitImport = async () => {
    if (!filename.trim() || !content.trim()) {
      setError('请填写文件名与文档内容')
      return
    }
    setImporting(true)
    setError('')
    setNotice('')
    try {
      const result = await importKnowledgeDoc(filename.trim(), content)
      setNotice(result.message ?? '导入成功')
      setFilename('')
      setContent('')
      onChanged?.()
      refresh()
    } catch (err) {
      setError(err instanceof ChatApiError ? err.message : '导入失败。')
    } finally {
      setImporting(false)
    }
  }

  const runSearch = async () => {
    if (!query.trim()) return
    setSearching(true)
    setSearchError('')
    try {
      const data = await searchKnowledge({
        query: query.trim(),
        doc: docFilter || null,
        k: 5,
      })
      setResults(data?.results ?? [])
    } catch (err) {
      setResults([])
      setSearchError(err instanceof ChatApiError ? err.message : '检索失败。')
    } finally {
      setSearching(false)
    }
  }

  const switchTab = (next) => {
    setTab(next)
    if (next === 'search') setDetail(null)
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal modal-wide"
        role="dialog"
        aria-modal="true"
        aria-label="知识库管理"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h3>📚 知识库管理</h3>
          <button type="button" className="modal-close" aria-label="关闭" onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="kb-tabs">
          <button
            type="button"
            className={`kb-tab ${tab === 'docs' ? 'active' : ''}`}
            onClick={() => switchTab('docs')}
          >
            📄 文档
          </button>
          <button
            type="button"
            className={`kb-tab ${tab === 'search' ? 'active' : ''}`}
            onClick={() => switchTab('search')}
          >
            🔍 检索测试
          </button>
        </div>

        {tab === 'search' ? (
          <div className="kb-search">
            <p className="modal-desc">
              输入问题，查看 RAG 实际检索命中了哪些知识库片段（不调用大模型）。
            </p>
            <div className="kb-search-row">
              <select
                className="kb-search-select"
                value={docFilter}
                onChange={(e) => setDocFilter(e.target.value)}
              >
                <option value="">全部文档</option>
                {docs.map((d) => (
                  <option key={d.name} value={d.name}>
                    {d.name}
                  </option>
                ))}
              </select>
              <input
                className="kb-search-input"
                placeholder="输入测试问题，如：私有化部署要什么环境？"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') runSearch()
                }}
                maxLength={500}
              />
              <button
                type="button"
                className="btn btn-primary btn-sm"
                disabled={searching || !query.trim()}
                onClick={runSearch}
              >
                {searching ? '检索中…' : '🔍 检索'}
              </button>
            </div>

            {searchError && <div className="form-error kb-error">⚠️ {searchError}</div>}

            {results.length > 0 && (
              <div className="kb-search-meta">
                命中 {results.length} 个片段{docFilter ? `（${docFilter}）` : ''}
              </div>
            )}

            <div className="kb-search-results">
              {results.length === 0 && !searching && (
                <div className="kb-empty">输入问题并点击检索，查看命中的片段。</div>
              )}
              {results.map((r, i) => (
                <div className="kb-hit" key={`${r.source}-${i}`}>
                  <div className="kb-hit-head">
                    <span className="kb-hit-src">
                      {r.source}/{r.heading}
                    </span>
                    <span className="kb-hit-score">相关度 {r.score}</span>
                  </div>
                  <div className="kb-hit-content">
                    <HighlightContent text={r.content} query={query} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : detail ? (
          <>
            <div className="kb-detail-head">
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setDetail(null)}>
                ← 返回列表
              </button>
              <span className="kb-detail-name">{detail.name}</span>
            </div>
            <pre className="kb-content">{detail.content}</pre>
          </>
        ) : (
          <>
            <p className="modal-desc">
              查看或导入 RAG 检索所用的 Markdown 文档。导入后立即生效。
            </p>

            {/* 导入区 */}
            <div className="kb-import">
              <div className="kb-import-row">
                <input
                  className="kb-filename"
                  placeholder="文件名，如 faq.md"
                  value={filename}
                  onChange={(e) => setFilename(e.target.value)}
                  maxLength={120}
                />
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => fileRef.current?.click()}
                >
                  📁 选择文件
                </button>
                <input
                  ref={fileRef}
                  type="file"
                  accept=".md,text/markdown,text/plain"
                  style={{ display: 'none' }}
                  onChange={onFilePicked}
                />
              </div>
              <textarea
                className="kb-textarea"
                placeholder="粘贴或编辑 Markdown 内容（选择文件会自动填充）……"
                value={content}
                onChange={(e) => setContent(e.target.value)}
              />
              <div className="kb-import-actions">
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  disabled={importing || !filename.trim() || !content.trim()}
                  onClick={submitImport}
                >
                  {importing ? '导入中…' : '⬆️ 导入文档'}
                </button>
              </div>
            </div>

            {notice && <div className="success-banner kb-notice">✅ {notice}</div>}
            {error && <div className="form-error kb-error">⚠️ {error}</div>}

            {/* 文档列表 */}
            <div className="kb-list">
              <div className="kb-list-head">
                <span>文档（{docs.length}）</span>
                {loading && <span className="kb-loading">加载中…</span>}
              </div>
              {docs.length === 0 && !loading && (
                <div className="kb-empty">暂无文档，导入一份 Markdown 开始吧。</div>
              )}
              {docs.map((doc) => (
                <div className="kb-doc" key={doc.name}>
                  <div className="kb-doc-info">
                    <span className="kb-doc-name">{doc.name}</span>
                    <span className="kb-doc-meta">{doc.chunks} 分块 · {doc.chars} 字符</span>
                  </div>
                  <div className="kb-doc-actions">
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => openDoc(doc.name)}>
                      查看
                    </button>
                    <button type="button" className="btn btn-danger btn-sm" onClick={() => removeDoc(doc.name)}>
                      删除
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
