/* 对话输入区状态:模式 / 附件 / 图片 / 知识摄入任务 / 全局轻提示 */
import { reactive } from 'vue'
import { getJSON, postForm } from '../api.js'

const MODE_KEY = 'sfx.chat.mode'
const MAX_MB = 20

export const chat = reactive({
  mode: localStorage.getItem(MODE_KEY) || 'stream', // stream | orchestrate
  attachments: [], // 已上传(带 text),随下一条消息发送
  images: [], // 本地 dataURL,发送时走 images 字段
  ingest: null, // {name,pct,status,ku,msg}
  toast: '',
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
