/* 对话与输入区状态:模式 / 附件 / 图片 / 摄入任务 / 消息流 / SSE */
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

  /* 对话 */
  messages: [], // [{role,content,thinking,evidence,error,ts,streaming,stage,plan}]
  sessionId: null,
  streamState: 'idle', // idle | streaming | done | error
  /* 错误落在具体那条消息的 m.error 上(界面上能看到 + 能点重试),
     这里不再留一个没人读的 lastError */
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

/* ── 摄入知识库:POST /knowledge/ingest/file/async → 轮询 /knowledge/ingest/jobs/{id}
      注意路径无 /api 前缀:vendor/hkc 的 knowledge router 是根路径挂载的。 ── */
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

/* 组件间的一次性信号:斜杠命令要调 ToolMenu 里的文件选择,又不能硬耦合。
   (/设置 不再走这里 —— 它现在直接切到设置面板,见 ChatDock 的命令表) */
export const ui = reactive({ ingestTick: 0 })
export function requestIngest() {
  ui.ingestTick += 1
}

/* 纯本地消息(/帮助 之类),不经过后端 */
export function pushLocal(content) {
  chat.messages.push({
    role: 'assistant', content, ts: Date.now(),
    thinking: '', evidence: [], error: '', notice: '', streaming: false,
  })
}

/* 认知搜索(HSR):POST /api/search/query,结果作为一张证据卡挂在消息流里 */
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
    /* internal_only=true:只查 HMR 记忆 + HKC 知识 + Agent 态势,不联网 —— 联网分支实测返回空图 */
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

/* 改标题:后端 rename 会把空串兜回「新会话」,这里先挡掉空提交,避免误清标题 */
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

/* 删会话。删的是当前会话时落回列表里的下一条;一条不剩就新开一个 ——
   否则界面会停在一个已经不存在的 sessionId 上,下一条消息直接 404。 */
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

/* 重发:把这条回复对应的提问找回来,截断到它之前再投一次(附件/图片一起还原) */
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
      /* 后端已无此会话 → 落到下面新建 */
    }
  }
  return newSession()
}

/* ══════════ 发送 / 流式 ══════════ */

let ctrl = null
/* 每次 send 领一个代号。切会话 / 新建会话时把代号推进,在跑的那一路回来发现
   代号变了就不再碰全局状态 —— 否则它会把新会话的 streamState 覆盖掉、还把 ctrl
   清空,导致新流停不下来,两路 token 同时往不同消息里写。 */
let gen = 0

/* 掐断在跑的流:切会话、新建会话、重试前都先调一次 */
function cutStream() {
  gen += 1
  if (ctrl) {
    try {
      ctrl.abort()
    } catch {
      /* 已经收尾了 */
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
  /* 后端只用最近 12 条拼上下文,与旧前端保持一致 */
  const history = chat.messages
    .filter((m) => m.content && m.role && !m.error)
    .slice(-12)
    .map((m) => ({ role: m.role, content: m.content }))

  chat.messages.push({
    role: 'user', content: text, ts: Date.now(), attachments, streaming: false, thinking: '',
    /* 存一份图片快照:重试时 send() 从 chat.images 取图,不带上就丢图了 */
    images: chat.images.map((im) => ({ ...im })),
  })
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
