import { reactive } from 'vue'
import { getJSON, getRaw, delJSON, patchJSON, postJSON } from '../api.js'
import { openDoc } from './app.js'

const safe = (p) => p.then((v) => v, () => null)

export const panels = reactive({
  activity: { state: 'idle', events: [], traces: [], thought: null },
  tasks: { state: 'idle', sched: {}, processes: [], procStats: {} },
  agents: { state: 'idle', list: [], info: null },
  tools: { state: 'idle', skills: [], md: [], breakers: {} },
  usage: { state: 'idle', tokens: null, gateway: {}, metrics: null, slow: [] },
  knowledge: {
    state: 'idle', stats: {}, index: {}, sources: [], abilities: [],
    recent: [], recentTotal: 0,
    ingest: 'idle', ingestMsg: '', ingestErr: '',
  },
  memory: { state: 'idle', stats: {}, recent: [], prefs: [] },
  review: {
    state: 'idle',
    stats: { cards: 0, learned: 0, fresh: 0, due: 0, avg_mastery: 0, load7: {}, risk: [] },
    queue: [],
    index: 0,
    revealed: false,
    session: 'idle',
    lastMsg: '',
  },
})

const LOADERS = {
  async activity(p) {
    const [bus, traces, thought] = await Promise.all([
      safe(getJSON('/bus/history?limit=40')),
      safe(getJSON('/traces?limit=25')),
      safe(getJSON('/thoughtchain/supervisor/summary')),
    ])
    p.events = bus?.events || []
    p.traces = (traces?.traces || []).slice().sort((a, b) => (b.duration_ms || 0) - (a.duration_ms || 0))
    p.thought = thought
    return !!(bus || traces || thought)
  },

  async tasks(p) {
    const [sched, proc] = await Promise.all([
      safe(getJSON('/scheduler/stats')),
      safe(getJSON('/processes?limit=30')),
    ])
    p.sched = sched || {}
    p.processes = proc?.processes || []
    p.procStats = proc?.stats || {}
    return !!(sched || proc)
  },

  async agents(p) {
    const [all, info] = await Promise.all([
      safe(getJSON('/status')),
      safe(getJSON('/system/info')),
    ])
    p.list = all ? Object.values(all) : []
    p.info = info
    return !!(all || info)
  },

  async tools(p) {
    const [sk, md, br] = await Promise.all([
      safe(getJSON('/ahs/skills')),
      safe(getJSON('/ahs/md-skills')),
      safe(getJSON('/ahs/breaker')),
    ])
    p.skills = sk?.skills || []
    p.md = md?.skills || []
    p.breakers = br?.breakers || {}
    return !!(sk || md || br)
  },

  async usage(p) {
    const [tk, gw, mt] = await Promise.all([
      safe(getJSON('/tokens')),
      safe(getJSON('/gateway/stats')),
      safe(getJSON('/metrics')),
    ])
    p.tokens = tk && !tk.error ? tk : null
    p.gateway = gw || {}
    p.metrics = mt
    p.slow = Object.entries(mt?.endpoints || {})
      .map(([name, v]) => ({ name, ...v }))
      .sort((a, b) => (b.p95_ms || 0) - (a.p95_ms || 0))
      .slice(0, 6)
    return !!(tk || gw || mt)
  },

  async knowledge(p) {
    const [st, src, ab, rc] = await Promise.all([
      safe(getRaw('/stats')),
      safe(getRaw('/knowledge/sources')),
      safe(getJSON('/knowledge/abilities')),
      safe(getJSON('/knowledge/recent?limit=50')),
    ])
    p.stats = st || {}
    p.index = st?.index_build || {}
    p.sources = src?.sources || []
    p.abilities = ab?.abilities || []
    p.recent = rc?.kus || []
    p.recentTotal = rc?.total ?? p.recent.length
    return !!(st || src || ab || rc)
  },

  async review(p) {
    const [st, q] = await Promise.all([
      safe(getJSON('/review/stats')),
      safe(getJSON('/review/queue?limit=20&fresh_limit=10')),
    ])
    if (st) p.stats = { ...p.stats, ...st }
    if (q?.cards && p.session !== 'running') {
      p.queue = q.cards
      p.index = 0
      p.revealed = false
      if (p.session === 'done') p.session = 'idle'
    }
    return !!(st || q)
  },

  async memory(p) {
    const [st, rc, pf] = await Promise.all([
      safe(getJSON('/memory/stats')),
      safe(getJSON('/memory/recent?limit=12')),
      safe(getJSON('/preferences')),
    ])
    p.stats = st || {}
    p.recent = rc?.memories || []
    p.prefs = pf?.preferences || []
    return !!(st || rc || pf)
  },
}

export async function loadPanel(id, force = false) {
  const p = panels[id]
  if (!p || !LOADERS[id]) return
  if (!force && p.state !== 'idle' && p.state !== 'error') return
  p.state = 'loading'
  let ok = false
  try { ok = await LOADERS[id](p) } catch { ok = false }
  p.state = ok ? 'ok' : 'error'
}

export const refreshPanel = (id) => loadPanel(id, true)

export async function toggleMdSkill(id, enabled) {
  await patchJSON(`/ahs/md-skills/${encodeURIComponent(id)}`, { enabled })
  await refreshPanel('tools')
}

export async function ingestKnowledge({ text, title = '', domain = '' }) {
  const p = panels.knowledge
  const body = (text || '').trim()
  if (!body) throw new Error('先写点内容再收录')

  p.ingest = 'busy'
  p.ingestMsg = ''
  p.ingestErr = ''
  try {
    const d = await postJSON('/knowledge/ingest', {
      text: body,
      source_title: (title || '').trim(),
      domain: (domain || '').trim(),
    })
    const counts = Object.entries(d?.counts || {})
      .map(([k, v]) => `${k} ${v}`)
      .join(' · ')
    p.ingest = 'ok'
    const n = d?.ku_count ?? 0
    p.ingestMsg = n
      ? `已抽取 ${n} 条知识单元${counts ? ` · ${counts}` : ''}`
      : '模型没从这段文字里抽出知识条目 —— 试试写得更具体(谁 / 是什么 / 为什么)'
    try { await LOADERS.knowledge(p); p.state = 'ok' } catch {}
    return d
  } catch (e) {
    p.ingest = 'error'
    p.ingestErr = String(e?.message || e)
    throw e
  }
}

export async function startReview() {
  const p = panels.review
  if (!p.queue.length) await loadPanel('review', true)
  if (!p.queue.length) return
  p.session = 'running'
  p.index = 0
  p.revealed = false
  p.lastMsg = ''
}

export function revealCard() {
  panels.review.revealed = true
}

export async function gradeCard(grade) {
  const p = panels.review
  const card = p.queue[p.index]
  if (!card) return null
  const d = await postJSON('/review/grade', { ku_id: card.ku_id, grade })
  const nd = d?.next_in_days ?? 1
  p.lastMsg = `${card.name} → ${nd > 1 ? `${nd} 天后` : '明天'}再复习`
  p.index += 1
  p.revealed = false
  if (p.index >= p.queue.length) p.session = 'done'
  const st = await getJSON('/review/stats').catch(() => null)
  if (st) p.stats = { ...p.stats, ...st }
  return d
}

export async function dropPreference(id) {
  await delJSON(`/preferences/${encodeURIComponent(id)}`)
  await refreshPanel('memory')
}

export async function openMdSkill(id) {
  const d = await getJSON(`/ahs/md-skills/${encodeURIComponent(id)}`)
  openDoc({
    id: `skill:${d.id}`,
    kind: 'text',
    lang: 'markdown',
    title: d.name || d.id,
    path: `skills/${d.id}/SKILL.md`,
    meta: `指令技能 · v${d.version || '—'} · trust ${d.trust || '—'}`,
    content: d.body || d.description || '(正文为空)',
  })
}

const KU_FIELDS = [
  ['statement', '陈述'],
  ['definition', '定义'],
  ['summary', '摘要'],
  ['description', '描述'],
  ['aliases', '别名'],
  ['verifiable', '可验证'],
  ['source', '来源'],
  ['source_type', '来源类型'],
  ['author', '作者'],
  ['year', '年份'],
  ['page', '页码'],
  ['quote', '原文引用'],
  ['supports', '支持'],
  ['contradicts', '矛盾'],
]

const kuVal = (v) => {
  if (Array.isArray(v)) return v.length ? v.join('、') : ''
  if (typeof v === 'boolean') return v ? '是' : '否'
  return v === 0 || v === '' || v == null ? '' : String(v)
}

export async function openKu(id) {
  const d = await getRaw(`/knowledge/ku/${encodeURIComponent(id)}`)
  const extra = d.extra || {}

  const rows = KU_FIELDS
    .map(([k, label]) => [label, kuVal(extra[k])])
    .filter(([, v]) => v)
  const body = rows.filter(([, v]) => v !== d.summary && v !== d.name)

  const parts = []
  if (body.length) parts.push(body.map(([k, v]) => `- **${k}**: ${v}`).join('\n'))
  if (extra.source_text) parts.push(`### 出处原文\n\n${extra.source_text}`)

  openDoc({
    id: `ku:${d.ku_id}`,
    kind: 'text',
    lang: 'markdown',
    title: d.name || d.ku_id,
    path: `knowledge/${d.ku_type}/${d.ku_id}`,
    meta: `${d.ku_type} · ${d.domain || '未分域'} · 置信度 ${Math.round((d.confidence || 0) * 100)}%`,
    content: parts.join('\n\n') || '这条知识单元没有附带正文,只有一个名称。',
  })
}

export async function openTokenReport() {
  const d = await getJSON('/tokens/report')
  openDoc({
    id: 'token-report',
    kind: 'code',
    lang: 'text',
    title: 'token-report',
    path: 'foxclaw/api/routes/system.py',
    meta: 'Token 消耗报告 · 实时生成',
    content: d.report || '(暂无报告)',
  })
}
