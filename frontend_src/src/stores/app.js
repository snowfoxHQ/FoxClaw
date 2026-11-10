import { reactive } from 'vue'
import { getJSON } from '../api.js'

export const state = reactive({
  sessions: [],
  sessionsState: 'idle', // idle | loading | error
  sessionId: null,

  module: 'overview',
  doc: null, // { id, title, kind:'text'|'code', lang, path, content, meta }

  railOpen: true,
  railMode: 'progress', // 'progress' | 'context'

  officeStats: { waypoints: 0, conflicts: 0, alerts: 0, active: 0, total: 0 },
})

export const MODULES = [
  { id: 'overview', label: '概览', tag: 'Overview', group: '' },
  { id: 'activity', label: '活动', tag: 'Activity', group: '工作台' },
  { id: 'tasks', label: '定时任务', tag: 'Tasks', group: '工作台', icon: 'clock' },
  { id: 'agents', label: '代理', tag: 'Agents', group: '工作台', icon: 'bot', beta: true },
  { id: 'tools', label: '技能·连接器', tag: 'Tools', group: '能力', icon: 'plug' },
  { id: 'usage', label: '使用情况', tag: 'Usage', group: '能力' },
  { id: 'knowledge', label: '学习库', tag: 'Knowledge', group: '知识', icon: 'book', quick: ['review', 'memory'] },
  { id: 'review', label: '复习', tag: 'Review', group: '知识', icon: 'review' },
  { id: 'memory', label: '记忆中心', tag: 'Memory', group: '知识', icon: 'memory' },
  { id: 'settings', label: '设置', tag: 'Settings', group: '系统' },
]

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

export function openDoc(doc) {
  state.doc = doc
  state.railMode = 'context'
  state.railOpen = true
}

export function closeDoc() {
  state.doc = null
  state.railMode = 'progress'
}
