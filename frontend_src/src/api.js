/* 所有请求走相对路径:dev 由 vite proxy 透到 8000,prod 与后端同源 —— 无需区分环境。 */
export const API = '/api'

/* FastAPI 错误统一带 {detail},直接抛出来比 "xxx -> 400" 有用得多 */
async function toError(r, path) {
  let msg = `${path} -> ${r.status}`
  try {
    const j = await r.json()
    if (j && j.detail) msg = String(j.detail)
  } catch {}
  return new Error(msg)
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

export async function postForm(path, form) {
  const r = await fetch(API + path, { method: 'POST', body: form })
  if (!r.ok) throw await toError(r, path)
  return r.json()
}
