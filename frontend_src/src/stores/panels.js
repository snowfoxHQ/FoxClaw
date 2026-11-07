/* 左栏菜单 8 项的数据层。
   一项一个 loader,内部每个端点单独 .catch —— 某个子系统挂了只让对应区块空着,
   不让整个面板变「后端未连接」。组件只负责渲染,不各自写 fetch。 */
import { reactive } from 'vue'
import { getJSON, getRaw, delJSON, patchJSON } from '../api.js'
import { openDoc } from './app.js'

const safe = (p) => p.then((v) => v, () => null)

export const panels = reactive({
  activity: { state: 'idle', events: [], traces: [], thought: null },
  tasks: { state: 'idle', sched: {}, processes: [], procStats: {} },
  agents: { state: 'idle', list: [], info: null },
  tools: { state: 'idle', skills: [], md: [], breakers: {} },
  usage: { state: 'idle', tokens: null, gateway: {}, metrics: null, slow: [] },
  knowledge: { state: 'idle', stats: {}, index: {}, sources: [], abilities: [] },
  memory: { state: 'idle', stats: {}, recent: [], prefs: [] },
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

  /* 知识库 = HKC 根路径 /stats 与 /knowledge/sources,加 Snowfoxclaw 侧能力清单 */
  async knowledge(p) {
    const [st, src, ab] = await Promise.all([
      safe(getRaw('/stats')),
      safe(getRaw('/knowledge/sources')),
      safe(getJSON('/knowledge/abilities')),
    ])
    p.stats = st || {}
    p.index = st?.index_build || {}
    p.sources = src?.sources || []
    p.abilities = ab?.abilities || []
    return !!(st || src || ab)
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

export async function openTokenReport() {
  const d = await getJSON('/tokens/report')
  openDoc({
    id: 'token-report',
    kind: 'code',
    lang: 'text',
    title: 'token-report',
    path: 'snowfoxclaw/api/routes/system.py',
    meta: 'Token 消耗报告 · 实时生成',
    content: d.report || '(暂无报告)',
  })
}
