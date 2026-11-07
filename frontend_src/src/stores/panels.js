/* 左栏菜单 8 项的数据层。
   一项一个 loader,内部每个端点单独 .catch —— 某个子系统挂了只让对应区块空着,
   不让整个面板变「后端未连接」。组件只负责渲染,不各自写 fetch。 */
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
    /* 最近录入的知识单元:右栏只读前 10 条,面板看全量 */
    recent: [], recentTotal: 0,
    /* 录入是同步 LLM 抽取,可能几十秒 —— 状态提到 store 里,
       切到别的面板再回来时不会以为「没提交过」 */
    ingest: 'idle', ingestMsg: '', ingestErr: '',
  },
  memory: { state: 'idle', stats: {}, recent: [], prefs: [] },
  /* SM-2 复习:stats 是全局账本,queue 是「今天这一轮要过的卡」。
     session 放 store 是因为复习到一半点开文档再回来,不能让进度归零。 */
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
  /* 活动 = 事件总线(系统发生了什么) + 请求链路(哪次调用慢) + 思考链健康度 */
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

  /* 任务 = 调度器记账 + 进程生命周期(后端把每个任务建模成进程) */
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

  /* 代理 = /api/status(全部 Agent 的 harness + 思考链) + 系统信息 */
  async agents(p) {
    const [all, info] = await Promise.all([
      safe(getJSON('/status')),
      safe(getJSON('/system/info')),
    ])
    p.list = all ? Object.values(all) : []
    p.info = info
    return !!(all || info)
  },

  /* 技能 = 内置/HTTP/MCP 技能表 + 指令技能(SKILL.md) + 熔断器 */
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

  /* 使用情况 = token 计量 + 网关配额 + 端点延迟(p95 最慢的几条) */
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

  /* 知识库 = HKC 根路径 /stats 与 /knowledge/sources,加 Foxclaw 侧能力清单
     与最近录入的 KU(右栏和面板共用这一份) */
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

  /* 复习 = SM-2 账本 + 今日队列。会话进行中只更新账本,不动队列 ——
     否则右栏刷新一次就把当前卡片换掉了。 */
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

  /* 记忆中心 = HMR 统计 + 最近记忆 + 用户偏好 */
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
  /* 已有数据就不重拉:切换菜单来回点不重复打后端;要新数据显式刷新 */
  if (!force && p.state !== 'idle' && p.state !== 'error') return
  p.state = 'loading'
  let ok = false
  try { ok = await LOADERS[id](p) } catch { ok = false }
  p.state = ok ? 'ok' : 'error'
}

export const refreshPanel = (id) => loadPanel(id, true)

/* ── 面板内的写操作(都是真实端点,不是本地假删除) ── */

/* 指令技能启停:PATCH /ahs/md-skills/{id} → 全量重拉一次保证和服务端一致 */
export async function toggleMdSkill(id, enabled) {
  await patchJSON(`/ahs/md-skills/${encodeURIComponent(id)}`, { enabled })
  await refreshPanel('tools')
}

/* 录入一段短知识:POST /knowledge/ingest → KDE 抽取成 KU → KEE 演化。
   同步 LLM 抽取,可能几十秒,所以状态挂在 store 上而不是组件里。
   成功后**静默重拉**:走 refreshPanel 会把 state 置回 loading,整面板闪骨架,
   而这里只想要新数据。 */
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
      /* 走到这里说明 LLM 调用是成功的(全失败会 502 进 catch),只是这段文字里
         没抽出 Concept/Fact/Claim/Entity。别提示"没配模型"误导用户去改 key。 */
      : '模型没从这段文字里抽出知识条目 —— 试试写得更具体(谁 / 是什么 / 为什么)'
    try { await LOADERS.knowledge(p); p.state = 'ok' } catch {}
    return d
  } catch (e) {
    p.ingest = 'error'
    p.ingestErr = String(e?.message || e)
    throw e
  }
}

/* ── SM-2 复习会话 ── */

/* 开一轮:队列空了就先拉一次。会话状态在 store 里,点开文档再回来不丢进度。 */
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

/* 评分 → 服务端按 SM-2 算下一次间隔 → 翻到下一张。
   四档对应 SM-2 的 0/3/4/5:0 完全忘记(重置)、3 勉强、4 记住、5 轻松。
   只更新账本不动队列 —— 队列是这一轮的现场。 */
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

/* 删除一条误存的偏好:DELETE /preferences/{id} */
export async function dropPreference(id) {
  await delJSON(`/preferences/${encodeURIComponent(id)}`)
  await refreshPanel('memory')
}

/* 技能 / KU 正文 → 中间舞台。kind 决定皮肤:code 走 CLI 黑底,text 走排版正文 */
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

/* extra 里的业务字段逐类型不同(Fact 有 statement、Evidence 有 source、Entity 有 aliases),
   按类型硬编码会漏;这里统一按「有值就列出来」处理,任何 KU 打开都有内容可读。 */
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

/* 单个 KU 的正文 → 中间舞台。走 HKC 根路径 /knowledge/ku/{id}
   (那条路径带 source_text,列表/图谱的响应为了瘦身把它剔掉了) */
export async function openKu(id) {
  const d = await getRaw(`/knowledge/ku/${encodeURIComponent(id)}`)
  const extra = d.extra || {}

  const rows = KU_FIELDS
    .map(([k, label]) => [label, kuVal(extra[k])])
    .filter(([, v]) => v)
  /* statement 已经在标题下一行重复过一次,正文里不再复读 */
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
