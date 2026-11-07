import { reactive } from 'vue'
import { getJSON } from '../api.js'

/* 单一状态源:所有栏只读这一份,不再有 _syncNav 那类「三个入口各改一遍」的补丁。 */
export const state = reactive({
  /* 左栏 */
  sessions: [],
  sessionsState: 'idle', // idle | loading | error
  sessionId: null,

  /* 中间舞台 */
  module: 'overview',
  doc: null, // { id, title, kind:'text'|'code', lang, path, content, meta }

  /* 右栏 Inspector:双内容态,同一栏 */
  railOpen: true,
  railMode: 'progress', // 'progress' | 'context'

  /* 办公进度(OfficeAgent 的态势与进程);学习账本在 panels.review 里 */
  officeStats: { waypoints: 0, conflicts: 0, alerts: 0, active: 0, total: 0 },
})

export const MODULES = [
  { id: 'overview', label: '概览', tag: 'Overview', group: '' },
  { id: 'activity', label: '活动', tag: 'Activity', group: '工作台' },
  { id: 'tasks', label: '定时任务', tag: 'Tasks', group: '工作台', icon: 'clock' },
  { id: 'agents', label: '代理', tag: 'Agents', group: '工作台', icon: 'bot', beta: true },
  { id: 'tools', label: '技能·连接器', tag: 'Tools', group: '能力', icon: 'plug' },
  { id: 'usage', label: '使用情况', tag: 'Usage', group: '能力' },
  /* 学习库 = 办公/学习的汇总表:知识库里有什么、哪些到期该复习、记了什么,一份数据的三个视图。
     所以它只有一个入口,hover 出「复习 / 记忆」两个快捷 —— 平级摆三行会让同一份数据看着像三份。 */
  { id: 'knowledge', label: '学习库', tag: 'Knowledge', group: '知识', icon: 'book', quick: ['review', 'memory'] },
  { id: 'review', label: '复习', tag: 'Review', group: '知识', icon: 'review' },
  { id: 'memory', label: '记忆中心', tag: 'Memory', group: '知识', icon: 'memory' },
  /* 设置不是"干活的地方",所以不进常驻栏;顶栏 ⚙ 与「更多」都能到,
     顶栏那个 ⚙ 之前是死按钮(title 写着 P3 接入)。 */
  /* 设置落在「更多」里,那里不画图标;之前挂的 icon:'gear' 在 LeftRail 的 ICONS 里
     并不存在(只有 book/clock/plug/bot/more/review/memory/dot),是个死字段。 */
  { id: 'settings', label: '设置', tag: 'Settings', group: '系统' },
]

/* 左栏常驻的功能入口(顺序即渲染顺序)。不进这个数组的模块全部落进底部「更多」浮层。
   常驻项恒可见 = 不用先点开菜单才知道有什么功能;代价是列高固定,所以只放四个。 */
export const RAIL = ['knowledge', 'tasks', 'tools', 'agents']

export const railItems = () => RAIL.map((id) => MODULES.find((m) => m.id === id)).filter(Boolean)
export const moreItems = () => MODULES.filter((m) => !RAIL.includes(m.id))

export const moduleLabel = (id) => MODULES.find((m) => m.id === id)?.label ?? id
export const moduleIcon = (id) => MODULES.find((m) => m.id === id)?.icon ?? 'dot'

export async function loadSessions() {
  state.sessionsState = 'loading'
  try {
    const d = await getJSON('/sessions?limit=50')
    state.sessions = d.sessions || []
    state.sessionsState = 'ok'
  } catch {
    state.sessions = []
    state.sessionsState = 'error'
  }
}

export function openSession(s) {
  state.sessionId = s.id
  state.doc = null
  state.railMode = 'progress'
}

export async function loadProgress() {
  /* 学习账本已改由 panels.review(/api/review/*)提供 —— SM-2 状态写在 KU.extra 里,
     和知识库是同一份数据;/learning/analysis 只统计 learning_note 一类条目,
     两套口径并存会让右栏数字和复习面板对不上。 */

  /* 办公 = OfficeAgent(行政职责已并入它)的全局态势 + 进程。
     「已摄入资料」已挪到右栏的「最近知识」(读 /api/knowledge/recent),
     书籍级列表留在知识库面板,这里不再重复拉 /knowledge/sources。 */
  const [sit, proc] = await Promise.all([
    getJSON('/orchestration/situation').catch(() => null),
    getJSON('/processes?limit=5').catch(() => null),
  ])
  state.officeStats = {
    waypoints: sit?.itinerary?.waypoints ?? 0,
    conflicts: sit?.itinerary?.conflicts ?? 0,
    alerts: (sit?.alerts || []).length,
    active: proc?.stats?.active ?? 0,
    total: proc?.stats?.total ?? 0,
  }
}

export function setModule(id) {
  state.module = id
  state.doc = null
}

/* 打开一份文件 → 正文进中间舞台(宽度够读代码),右栏同步切到检视态并自动展开 */
export function openDoc(doc) {
  state.doc = doc
  state.railMode = 'context'
  state.railOpen = true
}

export function closeDoc() {
  state.doc = null
  state.railMode = 'progress'
}
