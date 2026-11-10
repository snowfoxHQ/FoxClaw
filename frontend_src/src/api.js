export const API = '/api'

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
