import { reactive } from 'vue'
import { delJSON, getJSON, postJSON, putJSON } from '../api.js'

export const model = reactive({
  providers: [],
  custom: [],
  current: null,
  state: 'idle',
  err: '',
})

let loaded = false

export async function loadModels(force = false) {
  if (loaded && !force) return
  model.state = 'loading'
  try {
    const d = await getJSON('/llm/models')
    model.providers = d.providers || []
    model.custom = d.custom || []
    model.current = d.current || null
    model.state = 'ok'
    loaded = true
  } catch (e) {
    model.state = 'error'
    model.err = e.message
  }
}

export function currentLabel() {
  const c = model.current
  if (!c) return { name: 'Auto', color: '#7e93b8', sub: '跟随 .env 默认' }
  if (c.source === 'custom') {
    const m = model.custom.find((x) => x.name === c.name)
    return { name: c.name, color: '#a78bfa', sub: m ? m.model : '自定义' }
  }
  const p = model.providers.find((x) => x.provider === c.provider)
  return { name: p ? p.label : c.provider, color: p ? p.color : '#7e93b8', sub: c.provider }
}

export async function selectModel({ source, provider = null, name = null }) {
  const body = { source }
  if (provider) body.provider = provider
  if (name) body.name = name
  const r = await postJSON('/llm/select', body)
  model.current = r.current || null
  return r
}

export function modelOptions() {
  const c = model.current
  const out = [{
    source: 'auto', key: 'auto', label: 'Auto', sub: '跟随 .env 默认',
    color: '#7e93b8', active: !c,
  }]
  for (const p of model.providers) {
    out.push({
      source: 'preset', key: p.provider, label: p.label || p.provider, sub: p.provider,
      color: p.color || '#7e93b8',
      configured: p.configured !== false,
      active: !!c && c.source !== 'custom' && c.provider === p.provider,
    })
  }
  for (const m of model.custom) {
    out.push({
      source: 'custom', key: m.name, label: m.name, sub: m.model || '自定义',
      color: '#a78bfa',
      active: !!c && c.source === 'custom' && c.name === m.name,
    })
  }
  return out
}

export async function addCustom(payload) {
  await postJSON('/llm/custom/add', payload)
  await loadModels(true)
}

export async function testModel(payload) {
  return postJSON('/llm/test', payload)
}

export async function getCustom(name) {
  return getJSON(`/llm/custom/${encodeURIComponent(name)}`)
}

export async function updateCustom(name, payload) {
  await putJSON(`/llm/custom/${encodeURIComponent(name)}`, payload)
  await loadModels(true)
}

export async function removeCustom(name) {
  await delJSON(`/llm/custom/${encodeURIComponent(name)}`)
  if (model.current && model.current.name === name) model.current = null
  await loadModels(true)
}
