/* 所有请求走相对路径:dev 由 vite proxy 透到 8000,prod 与后端同源 —— 无需区分环境。 */
export const API = '/api'

export async function getJSON(path, init) {
  const r = await fetch(API + path, init)
  if (!r.ok) throw new Error(`${path} -> ${r.status}`)
  return r.json()
}

export async function postJSON(path, body) {
  return getJSON(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}
