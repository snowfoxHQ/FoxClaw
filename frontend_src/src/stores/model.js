/* 模型选择:全部走 /api/llm/*(llm_admin.py),切换后服务端立即生效,无需重启。 */
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

/* 设置面板里的模型清单:auto / 预设 / 自定义摊平成同一形状。
   active 的判定必须和上面的 currentLabel 一致 —— 两处各写一套,切换后 pill 和
   列表就会各说各的(这类"同一状态两套判定"是回归温床)。 */
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

/* 连通性测试:后端发一个 "hi" 走真实客户端,失败时 **不抛** 而是返回 {ok:false,error}
   —— 那正是要显示给用户看的东西,别当异常吞掉。 */
export async function testModel(payload) {
  return postJSON('/llm/test', payload)
}

/* 单个自定义模型详情:api_key 永不出网,只回 has_key(编辑表单里留空即保留原值) */
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
