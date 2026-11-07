/* 所有请求走相对路径:dev 由 vite proxy 透到 8000,prod 与后端同源 —— 无需区分环境。 */
export const API = '/api'

/* FastAPI 错误统一带 {detail},直接抛出来比 "xxx -> 400" 有用得多。
   detail 可能是字符串,也可能是结构化对象({error:"install_denied",reasons:[...]} /
   {needs_ack:true,warnings:[...]})—— 直接 String() 会得到 "[object Object]",
   所以拆一层;原始 detail / status 挂到 err 上,调用方要区分「要确认」才有据可依。 */
function detailMsg(d) {
  if (typeof d === 'string') return d
  if (d && typeof d === 'object') {
    const list = d.reasons || d.warnings || []
    const head = d.error || d.message || ''
    if (head || list.length) {
      return [head, ...(Array.isArray(list) ? list : [list])].filter(Boolean).join(' · ')
    }
    return JSON.stringify(d)
  }
  return ''
}

async function toError(r, path) {
  let msg = `${path} -> ${r.status}`
  let detail = null
  try {
    const j = await r.json()
    if (j && j.detail !== undefined) {
      detail = j.detail
      msg = detailMsg(j.detail) || msg
    }
  } catch {}
  const e = new Error(msg)
  e.status = r.status
  e.detail = detail
  return e
}

export async function getJSON(path, init) {
  const r = await fetch(API + path, init)
  if (!r.ok) throw await toError(r, path)
  return r.json()
}

export async function postJSON(path, body) {
  return getJSON(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function delJSON(path) {
  return getJSON(path, { method: 'DELETE' })
}

export async function patchJSON(path, body) {
  return getJSON(path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function putJSON(path, body) {
  return getJSON(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function postForm(path, form) {
  const r = await fetch(API + path, { method: 'POST', body: form })
  if (!r.ok) throw await toError(r, path)
  return r.json()
}

/* 少数端点在根路径、不带 /api 前缀 —— vendor/hkc 的 knowledge router 是直接
   include_router(knowledge.router) 挂上去的,实测 /api/knowledge/* 是 404。 */
export async function getRaw(path, init) {
  const r = await fetch(path, init)
  if (!r.ok) throw await toError(r, path)
  return r.json()
}

export async function postFormRaw(path, form) {
  const r = await fetch(path, { method: 'POST', body: form })
  if (!r.ok) throw await toError(r, path)
  return r.json()
}
