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

  /* 学习/办公 进度(学习走真实 /learning/analysis;办公在 P2 接) */
  learning: [],
  learnStats: { total: 0, avgMastery: 0, due: 0, risk: 0 },
  office: [],
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
  /* TODO(P2): 办公资料/进度接真实接口,现阶段为空占位,不伪造数据 */
  state.office = []
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
