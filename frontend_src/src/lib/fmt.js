/* 7 个面板共用的时间/数字格式化。集中一份,避免每个组件各写一遍还写得不一样。 */

/* 后端时间戳三种形态都有:秒 float(事件总线)、毫秒 int、ISO 字符串(记忆 created_at)。
   字符串交给 Date 自己解析,数字按量级判断。 */
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

/* 截断到 n 个字符(中文按 1 计),用于行内摘要 */
export function clip(s, n = 46) {
  const t = String(s ?? '').replace(/\s+/g, ' ').trim()
  return t.length > n ? t.slice(0, n) + '…' : t
}
