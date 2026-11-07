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

  /* 学习/办公 进度(学习走 /learning/analysis;办公走 OfficeAgent 的态势与进程) */
  learning: [],
  learnStats: { total: 0, avgMastery: 0, due: 0, risk: 0 },
  officeStats: { waypoints: 0, conflicts: 0, alerts: 0, active: 0, total: 0 },
  progressState: 'idle',
})

export const MODULES = [
  { id: 'overview', label: '概览', tag: 'Overview', group: '' },
  { id: 'activity', label: '活动', tag: 'Activity', group: '工作台' },
  { id: 'tasks', label: '任务', tag: 'Tasks', group: '工作台' },
  { id: 'agents', label: '代理', tag: 'Agents', group: '工作台' },
  { id: 'tools', label: '技能', tag: 'Tools', group: '能力' },
  { id: 'usage', label: '使用情况', tag: 'Usage', group: '能力' },
  { id: 'knowledge', label: '知识库', tag: 'Knowledge', group: '知识' },
  { id: 'memory', label: '记忆中心', tag: 'Memory', group: '知识' },
]

export const moduleLabel = (id) => MODULES.find((m) => m.id === id)?.label ?? id

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
  state.progressState = 'loading'
  try {
    const d = await getJSON('/learning/analysis')
    state.learning = (d.items || []).slice(0, 5).map((k) => ({
      title: k.title || k.name || '知识条目',
      tag: k.topic || k.tag || '学习',
    }))
    state.learnStats = {
      total: d.total_items || 0,
      avgMastery: Math.round((d.avg_mastery || 0) * 100),
      due: d.due_today || 0,
      risk: (d.forgetting_risk || []).length,
    }
    state.progressState = 'ok'
  } catch {
    state.learning = []
    state.progressState = 'error'
  }

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
