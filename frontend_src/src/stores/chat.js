import { reactive } from 'vue'
import { API, delJSON, getJSON, getRaw, postForm, postFormRaw, postJSON } from '../api.js'
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

  messages: [], // [{role,content,thinking,evidence,error,ts,streaming,stage,plan}]
  sessionId: null,
  streamState: 'idle', // idle | streaming | done | error
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

export async function ingestFile(file, collection = '') {
  if (!file) return
  if (chat.ingest) return say('等当前文件摄入完成再传下一个')
  chat.ingest = { name: file.name, pct: 0, status: 'queued', ku: 0 }
  const fd = new FormData()
  fd.append('file', file)
  if (collection) fd.append('collection', collection)
  let jobId
  try {
    jobId = (await postFormRaw('/knowledge/ingest/file/async', fd)).job_id
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
      const j = await getRaw(`/knowledge/ingest/jobs/${jobId}`)
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

export async function bootChat() {
  const saved = localStorage.getItem(SID_KEY)
  if (saved) await loadSession(saved, { quiet: true })
}

export async function loadSession(id, { quiet = false } = {}) {
  if (!id) return
  cutStream()
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

export const ui = reactive({ ingestTick: 0 })
export function requestIngest() {
  ui.ingestTick += 1
}

export function pushLocal(content) {
  chat.messages.push({
    role: 'assistant', content, ts: Date.now(),
    thinking: '', evidence: [], error: '', notice: '', streaming: false,
  })
}

export async function runSearch(rawQuery) {
  const query = String(rawQuery || '').trim()
  if (!query) return say('用法:/搜索 关键词')
  if (chat.streamState === 'streaming') return say('正在生成,先点停止')

  chat.messages.push({
    role: 'user', content: `/搜索 ${query}`, ts: Date.now(),
    attachments: [], thinking: '', streaming: false,
  })
  chat.messages.push({
    role: 'assistant', content: '检索中…', ts: Date.now(), search: null,
    thinking: '', evidence: [], error: '', notice: '', streaming: false, stage: '检索',
  })
  const msg = chat.messages[chat.messages.length - 1]

  try {
    const d = await postJSON('/search/query', { query, agent_id: 'supervisor', internal_only: true })
    const nodes = (d.graph && d.graph.nodes) || []
    msg.search = {
      route: d.route || 'HSR',
      confidence: d.confidence || 0,
      iterations: d.iterations || 0,
      nodes: nodes.slice(0, 6).map((n) => ({
        fact: n.fact, source: n.source, origin: n.origin, confidence: n.confidence,
      })),
    }
    msg.content = nodes.length
      ? `命中 ${nodes.length} 条证据 · 整体置信度 ${Math.round((d.confidence || 0) * 100)}%`
      : '没有检索到相关证据'
  } catch (e) {
    msg.content = ''
    msg.error = `检索失败:${e.message}`
  } finally {
    msg.stage = ''
  }
}

export async function newSession() {
  cutStream()
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

export async function renameSession(id, title) {
  const t = String(title || '').trim().slice(0, 60)
  if (!id || !t) return
  try {
    await postJSON(`/sessions/${encodeURIComponent(id)}/rename`, { title: t })
    const s = app.sessions.find((x) => x.id === id)
    if (s) s.title = t
  } catch (e) {
    say(`重命名失败:${e.message}`)
  }
}

export async function deleteSession(id) {
  if (!id) return
  try {
    await delJSON(`/sessions/${encodeURIComponent(id)}`)
  } catch (e) {
    return say(`删除失败:${e.message}`)
  }
  app.sessions = app.sessions.filter((s) => s.id !== id)
  if (chat.sessionId !== id) return
  const next = app.sessions[0]
  if (next) await loadSession(next.id)
  else await newSession()
}

export async function retry(msg) {
  if (chat.streamState === 'streaming') return say('正在生成,先点停止')
  const i = chat.messages.indexOf(msg)
  if (i < 0) return
  let u = -1
  for (let k = i; k >= 0; k -= 1) {
    if (chat.messages[k].role === 'user') {
      u = k
      break
    }
  }
  if (u < 0) return say('找不到对应的提问,无法重试')
  const q = chat.messages[u]
  const text = q.content
  chat.messages.splice(u)
  chat.attachments = (q.attachments || []).map((a) => ({ ...a, kind: 'file' }))
  chat.images = (q.images || []).map((im) => ({ ...im }))
  await send(text)
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
    }
  }
  return newSession()
}

let ctrl = null
let gen = 0

function cutStream() {
  gen += 1
  if (ctrl) {
    try {
      ctrl.abort()
    } catch {
    }
    ctrl = null
  }
  for (const m of chat.messages) if (m.streaming) m.streaming = false
  chat.streamState = 'idle'
}

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
  const history = chat.messages
    .filter((m) => m.content && m.role && !m.error)
    .slice(-12)
    .map((m) => ({ role: m.role, content: m.content }))

  chat.messages.push({
    role: 'user', content: text, ts: Date.now(), attachments, streaming: false, thinking: '',
    images: chat.images.map((im) => ({ ...im })),
  })
  chat.messages.push({
    role: 'assistant', content: '', thinking: '', evidence: [], error: '', notice: '',
    ts: Date.now(), streaming: true, stage: '', plan: null,
  })
  const reply = chat.messages[chat.messages.length - 1]
  chat.attachments = []
  chat.images = []
  chat.streamState = 'streaming'

  const orchestrate = chat.mode === 'orchestrate'
  const url = orchestrate ? '/task/stream' : '/chat/stream'
  const body = orchestrate
    ? { task: text, agent_id: 'supervisor', session_id: sid, context: {} }
    : { task: text, agent_id: 'supervisor', history, session_id: sid, images, attachments }

  const my = ++gen
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
    if (my !== gen) return
    reply.streaming = false
    chat.streamState = 'done'
  } catch (e) {
    if (my !== gen) return // 已被切会话/新请求作废,不要动现在的状态
    reply.streaming = false
    if (e && e.name === 'AbortError') {
      chat.streamState = 'done'
      if (!reply.content) reply.error = '已停止'
    } else {
      reply.error = (e && e.message) || '请求失败'
      chat.streamState = 'error'
    }
  } finally {
    if (my === gen) {
      ctrl = null
      loadSessions()
    }
  }
}

function applyEvent(msg, name, ev) {
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
