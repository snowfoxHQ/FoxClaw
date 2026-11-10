
function toDate(ts) {
  if (!ts) return null
  if (typeof ts === 'string') {
    const d = new Date(ts)
    return Number.isNaN(d.getTime()) ? null : d
  }
  const ms = ts > 1e12 ? ts : ts * 1000
  const d = new Date(ms)
  return Number.isNaN(d.getTime()) ? null : d
}

export function fmtTime(ts) {
  const d = toDate(ts)
  if (!d) return ''
  return d.toLocaleString('zh-CN', {
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

export function fmtAgo(ts) {
  const d = toDate(ts)
  if (!d) return ''
  const s = Math.max(0, (Date.now() - d.getTime()) / 1000)
  if (s < 60) return '刚刚'
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`
  return `${Math.floor(s / 86400)} 天前`
}

export function fmtNum(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return '0'
  return Number(n).toLocaleString('zh-CN')
}

export function fmtMs(ms) {
  if (ms === null || ms === undefined) return '—'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}

export function clip(s, n = 46) {
  const t = String(s ?? '').replace(/\s+/g, ' ').trim()
  return t.length > n ? t.slice(0, n) + '…' : t
}
