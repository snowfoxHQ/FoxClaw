/* 模型选择:全部走 /api/llm/*(llm_admin.py),切换后服务端立即生效,无需重启。 */
import { reactive } from 'vue'
import { delJSON, getJSON, postJSON } from '../api.js'

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

/* 当前 pill 上显示的名字与色点 */
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

export async function addCustom(payload) {
  await postJSON('/llm/custom/add', payload)
  await loadModels(true)
}

export async function removeCustom(name) {
  await delJSON(`/llm/custom/${encodeURIComponent(name)}`)
  if (model.current && model.current.name === name) model.current = null
  await loadModels(true)
}
