
const esc = (s) =>
  String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')

const RE_FENCE = /^\s*(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/
const RE_HEADING = /^(#{1,6})\s+(.*)$/
const RE_HR = /^\s*([-*_])\1{2,}\s*$/
const RE_LI = /^\s*(?:[-*+]|\d+[.)])\s+(.*)$/
const RE_OL = /^\s*\d+[.)]\s+/
const RE_QUOTE = /^>\s?(.*)$/
const RE_TABLE_ROW = /^\s*\|.*\|\s*$/
const RE_TABLE_SEP = /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/

/* 行内标记。先把 `code` 抽成占位符,免得代码里的 ** 被当成粗体。 */
function inline(src) {
  const codes = []
  let out = esc(src).replace(/`([^`]+)`/g, (_, c) => {
    codes.push(c)
    return `\u0000${codes.length - 1}\u0000`
  })

  out = out
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^\w*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>')
    .replace(/~~([^~\n]+)~~/g, '<del>$1</del>')
    .replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (m, text, href) =>
      /^https?:\/\//i.test(href)
        ? `<a href="${href}" target="_blank" rel="noreferrer">${text}</a>`
        : m
    )

  return out.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[Number(i)]}</code>`)
}

function codeBlock(lang, body, open) {
  const cls = open ? 'md-code md-open' : 'md-code'
  const attr = lang ? ` data-lang="${esc(lang)}"` : ''
  return `<pre class="${cls}"${attr}><code>${esc(body)}</code></pre>`
}

function splitRow(line) {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((c) => c.trim())
}

function renderTable(rows) {
  if (!rows.length) return ''
  const head = rows[0]
  const body = rows.slice(1)
  const th = head.map((c) => `<th>${inline(c)}</th>`).join('')
  const trs = body
    .map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`)
    .join('')
  return `<table><thead><tr>${th}</tr></thead>${body.length ? `<tbody>${trs}</tbody>` : ''}</table>`
}

export function mdToHTML(src) {
  const text = String(src ?? '').replace(/\r\n?/g, '\n').replace(/\n$/, '')
  if (!text.trim()) return ''

  const lines = text.split('\n')
  const out = []
  let para = []
  let list = null // { tag, items }
  let quote = []

  const flushPara = () => {
    if (!para.length) return
    out.push(`<p>${para.map(inline).join('<br>')}</p>`)
    para = []
  }
  const flushList = () => {
    if (!list) return
    out.push(`<${list.tag}>${list.items.map((t) => `<li>${t}</li>`).join('')}</${list.tag}>`)
    list = null
  }
  const flushQuote = () => {
    if (!quote.length) return
    out.push(`<blockquote>${quote.map(inline).join('<br>')}</blockquote>`)
    quote = []
  }
  const flush = () => {
    flushPara()
    flushList()
    flushQuote()
  }

  let i = 0
  while (i < lines.length) {
    const line = lines[i].replace(/\s+$/, '')

    if (!line.trim()) {
      flush()
      i += 1
      continue
    }

    const fence = RE_FENCE.exec(line)
    if (fence) {
      flush()
      const mark = fence[1][0]
      const lang = fence[2] || ''
      const body = []
      let closed = false
      let j = i + 1
      for (; j < lines.length; j += 1) {
        const cur = lines[j]
        const end = RE_FENCE.exec(cur)
        if (end && end[1][0] === mark) {
          closed = true
          break
        }
        body.push(cur)
      }
      out.push(codeBlock(lang, body.join('\n'), !closed))
      i = closed ? j + 1 : j
      continue
    }

    if (RE_TABLE_ROW.test(line) && i + 1 < lines.length && RE_TABLE_SEP.test(lines[i + 1]) && lines[i + 1].includes('-')) {
      flush()
      const rows = [splitRow(line)]
      let j = i + 2
      while (j < lines.length && RE_TABLE_ROW.test(lines[j])) {
        rows.push(splitRow(lines[j]))
        j += 1
      }
      out.push(renderTable(rows))
      i = j
      continue
    }

    const h = RE_HEADING.exec(line)
    if (h) {
      flush()
      const lv = Math.min(h[1].length + 1, 5)
      out.push(`<h${lv}>${inline(h[2])}</h${lv}>`)
      i += 1
      continue
    }

    if (RE_HR.test(line)) {
      flush()
      out.push('<hr />')
      i += 1
      continue
    }

    const li = RE_LI.exec(line)
    if (li) {
      flushPara()
      flushQuote()
      const tag = RE_OL.test(line) ? 'ol' : 'ul'
      if (!list || list.tag !== tag) {
        flushList()
        list = { tag, items: [] }
      }
      list.items.push(inline(li[1]))
      i += 1
      continue
    }

    const q = RE_QUOTE.exec(line)
    if (q) {
      flushPara()
      flushList()
      quote.push(q[1])
      i += 1
      continue
    }

    flushList()
    flushQuote()
    para.push(line.trim())
    i += 1
  }

  flush()
  return out.join('')
}

export { esc }
