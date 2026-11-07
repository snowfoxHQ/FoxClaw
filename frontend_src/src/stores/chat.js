/* 对话与输入区状态:模式 / 附件 / 图片 / 摄入任务 / 消息流 / SSE */
import { reactive } from 'vue'
import { API, getJSON, postForm, postJSON } from '../api.js'
import { loadSessions, state as app } from './app.js'

const MODE_KEY = 'sfx.chat.mode'
const SID_KEY = 'sfx.sessionId'
const MAX_MB = 20

export const chat = reactive({
  mode: localStorage.getItem(MODE_KEY) || 'stream', // stream | orchestrate
  attachments: [], // 已上传(带 text),随下一条消息发送
  images: [], // 本地 dataURL,发送时转 {media_type,data}
  ingest: null, // {name,pct,status,ku,msg}
  toast: '',

  /* 对话 */
  messages: [], // [{role,content,thinking,evidence,error,ts,streaming,stage,plan}]
  sessionId: null,
  streamState: 'idle', // idle | streaming | done | error
  lastError: '',
})

let toastTimer = 0

export function say(msg, ms = 2800) {
  chat.toast = String(msg)
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (chat.toast = ''), ms)
}

export function setMode(m) {
  chat.mode = m
  localStorage.setItem(MODE_KEY, m)
}

/* ── 附件:POST /api/upload → {id,name,size,mime,url,text} ── */
export async function attachFile(file) {
  if (!file) return
  if (file.size > MAX_MB * 1048576) return say(`${file.name} 超过 ${MAX_MB}MB`)
  const fd = new FormData()
  fd.append('file', file)
  try {
    const a = await postForm('/upload', fd)
    chat.attachments.push({ ...a, kind: 'file' })
    say(`已附加 ${a.name}${a.text ? '' : '(二进制,仅元数据)'}`)
  } catch (e) {
    say(`上传失败:${e.message}`)
  }
}

/* ── 图片:本地转 dataURL,不落盘 ── */
export function attachImage(file) {
  if (!file) return
  if (!file.type.startsWith('image/')) return say('不是图片文件')
  const reader = new FileReader()
  reader.onload = () => chat.images.push({ name: file.name, url: String(reader.result) })
  reader.onerror = () => say(`读取失败:${file.name}`)
  reader.readAsDataURL(file)
}

export function dropAttachment(i) {
  chat.attachments.splice(i, 1)
}
export function dropImage(i) {
  chat.images.splice(i, 1)
}

/* ── 摄入知识库:POST /api/knowledge/ingest/file/async → 轮询 jobs/{id} ── */
export async function ingestFile(file, collection = '') {
  if (!file) return
  if (chat.ingest) return say('等当前文件摄入完成再传下一个')
  chat.ingest = { name: file.name, pct: 0, status: 'queued', ku: 0 }
  const fd = new FormData()
  fd.append('file', file)
  if (collection) fd.append('collection', collection)
  let jobId
  try {
    jobId = (await postForm('/knowledge/ingest/file/async', fd)).job_id
  } catch (e) {
    chat.ingest = { ...chat.ingest, status: 'error', msg: e.message }
    return say(`摄入失败:${e.message}`)
  }
  pollIngest(jobId)
}

export async function ingestFolder(files) {
  const list = [...files].filter(Boolean)
  for (const f of list) {
    await ingestFile(f)
    /* ingestFile 内部已串行守卫:等当前 job 结束再投下一个 */
    while (chat.ingest && chat.ingest.status !== 'error') {
      if (chat.ingest.status === 'done') break
      await new Promise((r) => setTimeout(r, 600))
    }
    chat.ingest = null
  }
  if (list.length) say(`文件夹摄入完成 · ${list.length} 个文件`)
}

async function pollIngest(jobId) {
  const deadline = Date.now() + 10 * 60 * 1000
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 900))
    try {
      const j = await getJSON(`/knowledge/ingest/jobs/${jobId}`)
      const pct = j.total
        ? Math.round((j.processed / j.total) * 100)
        : j.status === 'done'
          ? 100
          : 0
      chat.ingest = { name: j.filename, pct, status: j.status, ku: j.ku_count, msg: j.error }
      if (j.status === 'done') return say(`${j.filename} 摄入完成 · ${j.ku_count} 条`)
      if (j.status === 'error') return say(`${j.filename} 摄入失败:${j.error || '未知'}`)
    } catch (e) {
      chat.ingest = null
      return say(`进度查询失败:${e.message}`)
    }
  }
  chat.ingest = null
  say('摄入超时,后台可能仍在跑')
}

/* ══════════ 会话 ══════════ */

const STAGE_CN = {
  perceive: '感知', recall: '召回', meta_check: '自检', schedule: '排程',
  route: '路由', plan: '规划', dispatch: '分发', act: '执行',
  aggregate: '汇总', verify: '核验', reflect: '反思',
}

function normMessage(m) {
  return {
    role: m.role,
    content: m.content || '',
    ts: (m.ts || 0) * 1000,
    attachments: m.attachments || [],
    thinking: '',
    evidence: [],
    error: '',
    streaming: false,
  }
}

function adoptSession(id) {
  chat.sessionId = id
  app.sessionId = id
  localStorage.setItem(SID_KEY, id)
}

/* 启动时恢复上次会话(后端 sessions 落盘,thinking/evidence 不落盘) */
export async function bootChat() {
  const saved = localStorage.getItem(SID_KEY)
  if (saved) await loadSession(saved, { quiet: true })
}

export async function loadSession(id, { quiet = false } = {}) {
  if (!id) return
  try {
    const d = await getJSON(`/sessions/${encodeURIComponent(id)}`)
    adoptSession(id)
    chat.messages = (d.messages || []).map(normMessage)
    chat.streamState = 'idle'
  } catch (e) {
    if (chat.sessionId === id) {
      chat.sessionId = null
      localStorage.removeItem(SID_KEY)
    }
    if (!quiet) say(`会话加载失败:${e.message}`)
  }
}

export async function newSession() {
  try {
    const d = await postJSON('/sessions', {})
    adoptSession(d.id)
    chat.messages = []
    chat.streamState = 'idle'
    await loadSessions()
    return d.id
  } catch (e) {
    say(`新建会话失败:${e.message}`)
    return null
  }
}

async function ensureSession() {
  if (chat.sessionId) return chat.sessionId
  const saved = localStorage.getItem(SID_KEY)
  if (saved) {
    try {
      await getJSON(`/sessions/${saved}`)
      adoptSession(saved)
      return saved
    } catch {
      /* 后端已无此会话 → 落到下面新建 */
    }
  }
  return newSession()
}

/* ══════════ 发送 / 流式 ══════════ */

let ctrl = null

export function stop() {
  if (ctrl) ctrl.abort()
}

function dataURLToImage(url) {
  const m = /^data:([^;]+);base64,(.*)$/.exec(url || '')
  return m ? { media_type: m[1], data: m[2] } : null
}

export async function send(rawText) {
  const text = String(rawText ?? '').trim()
  if (chat.streamState === 'streaming') return say('正在生成,先点停止')
  if (!text && !chat.attachments.length && !chat.images.length) return

  const sid = await ensureSession()
  if (!sid) return

  const images = chat.images.map((im) => dataURLToImage(im.url)).filter(Boolean)
  const attachments = chat.attachments.map((a) => ({
    name: a.name, size: a.size, mime: a.mime, url: a.url, text: a.text || '',
  }))
  /* 后端只用最近 12 条拼上下文,与旧前端保持一致 */
  const history = chat.messages
    .filter((m) => m.content && m.role && !m.error)
    .slice(-12)
    .map((m) => ({ role: m.role, content: m.content }))

  chat.messages.push({ role: 'user', content: text, ts: Date.now(), attachments, streaming: false, thinking: '' })
  chat.messages.push({
    role: 'assistant', content: '', thinking: '', evidence: [], error: '', notice: '',
    ts: Date.now(), streaming: true, stage: '', plan: null,
  })
  /* 必须拿回数组里的响应式代理:直接改 push 进去的原始对象不会触发重渲染,
     表现就是气泡永远停在「···」而内容其实早到了 */
  const reply = chat.messages[chat.messages.length - 1]
  chat.attachments = []
  chat.images = []
  chat.streamState = 'streaming'
  chat.lastError = ''

  const orchestrate = chat.mode === 'orchestrate'
  const url = orchestrate ? '/task/stream' : '/chat/stream'
  const body = orchestrate
    ? { task: text, agent_id: 'supervisor', session_id: sid, context: {} }
    : { task: text, agent_id: 'supervisor', history, session_id: sid, images, attachments }

  ctrl = new AbortController()
  try {
    const r = await fetch(API + url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    })
    if (!r.ok) throw new Error(`${url} -> ${r.status}`)
    await readSSE(r, (name, ev) => applyEvent(reply, name, ev))
    reply.streaming = false
    chat.streamState = 'done'
  } catch (e) {
    reply.streaming = false
    if (e && e.name === 'AbortError') {
      chat.streamState = 'done'
      if (!reply.content) reply.error = '已停止'
    } else {
      reply.error = (e && e.message) || '请求失败'
      chat.streamState = 'error'
      chat.lastError = reply.error
    }
  } finally {
    ctrl = null
    loadSessions()
  }
}

function applyEvent(msg, name, ev) {
  /* 命名事件与 JSON 键双判据:后端明确要求两者都认 */
  if (name === 'thinking' || ev.thinking) {
    msg.thinking += ev.thinking || ''
    return
  }
  if (name === 'evidence' || ev.evidence) {
    msg.evidence = ev.evidence || []
    return
  }
  if (ev.delta) {
    msg.content += ev.delta
    return
  }
  if (ev.stage) {
    msg.stage = STAGE_CN[ev.stage] || ev.stage
    return
  }
  if (ev.plan) {
    msg.plan = ev.plan
    return
  }
  if (ev.result) {
    finalize(msg, ev.result)
    return
  }
  if (ev.error) {
    msg.error = ev.error
    return
  }
  if (ev.done) msg.streaming = false
}

function finalize(msg, res) {
  if (!res || typeof res !== 'object') return
  if (res.interrupted) return void (msg.error = '管线已中断')
  if (res.error === 'harness_blocked') return void (msg.error = '被 harness 策略拦截')
  if (res.status === 'needs_confirmation') msg.notice = '需要确认后才继续'
  const out = res.res ?? res.result ?? res.answer
  if (out && !msg.content) {
    msg.content = typeof out === 'string' ? out : JSON.stringify(out, null, 2)
  }
}

async function readSSE(res, onEvent) {
  if (!res.body) throw new Error('响应无流式主体')
  const reader = res.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    let idx
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      if (!block.trim()) continue
      const name = (block.match(/^event:\s*(\S+)/m) || [])[1] || 'message'
      const data = ((block.match(/^data:\s*([\s\S]*)$/m) || [])[1] || '').trim()
      if (!data) continue
      let ev
      try {
        ev = JSON.parse(data)
      } catch {
        continue
      }
      onEvent(name, ev)
    }
  }
}
