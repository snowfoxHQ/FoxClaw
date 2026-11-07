<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  addCustom, currentLabel, getCustom, loadModels, model, removeCustom, selectModel,
  testModel, updateCustom,
} from '../stores/model.js'
import { say } from '../stores/chat.js'

const open = ref(false)
const root = ref(null)
const form = ref(null)
const busy = ref(false)
const cur = computed(currentLabel)

onMounted(loadModels)

function toggle() {
  open.value = !open.value
  if (open.value) {
    form.value = null
    loadModels(true)
  }
}

function onDocDown(e) {
  if (root.value && !root.value.contains(e.target)) open.value = false
}
onMounted(() => document.addEventListener('mousedown', onDocDown, true))
onBeforeUnmount(() => document.removeEventListener('mousedown', onDocDown, true))

async function pick(payload) {
  if (busy.value) return
  busy.value = true
  try {
    await selectModel(payload)
    open.value = false
    say(`已切换到 ${currentLabel().name}`)
  } catch (e) {
    say(`切换失败:${e.message}`)
  } finally {
    busy.value = false
  }
}

function startAdd() {
  form.value = { edit: false, name: '', base_url: '', api_key: '', model: '', note: '' }
}

/* 编辑:先 GET 详情预填(key 只回 has_key,输入框留空 = 保留原值,后端语义一致) */
async function startEdit(m) {
  busy.value = true
  try {
    const d = await getCustom(m.name)
    form.value = {
      edit: true, original: d.name || m.name, name: d.name || m.name,
      base_url: d.base_url || '', api_key: '', model: d.model || '', note: d.note || '',
      has_key: !!d.has_key,
    }
  } catch (e) {
    say(`读取失败:${e.message}`)
  } finally {
    busy.value = false
  }
}

async function submit() {
  const f = form.value
  if (!f.name.trim() || !f.base_url.trim() || !f.model.trim()) {
    return say('名称 / Base URL / 模型 ID 都必填')
  }
  if (!f.edit && !f.api_key.trim()) return say('Key 必填')
  busy.value = true
  try {
    if (f.edit) {
      const payload = { base_url: f.base_url.trim(), model: f.model.trim(), note: f.note || '' }
      if (f.api_key.trim()) payload.api_key = f.api_key.trim()
      await updateCustom(f.original, payload)
      say('已保存修改')
    } else {
      await addCustom({
        name: f.name.trim(), base_url: f.base_url.trim(),
        api_key: f.api_key.trim(), model: f.model.trim(), note: f.note || '',
      })
      say('已添加自定义模型')
    }
    form.value = null
  } catch (e) {
    say(`${f.edit ? '保存' : '添加'}失败:${e.message}`)
  } finally {
    busy.value = false
  }
}

/* 连通性测试:POST /llm/test,后端失败也返回 200 + {ok:false,error},原样显示原因 */
const tState = ref('idle')   // idle | busy | ok | err
const tKey = ref('')
const tMsg = ref('')

async function test(source, key) {
  if (tState.value === 'busy') return
  tState.value = 'busy'
  tKey.value = `${source}:${key || ''}`
  tMsg.value = ''
  try {
    const d = source === 'custom'
      ? await testModel({ source: 'custom', name: key })
      : await testModel({ source: 'preset', provider: key })
    if (d.ok) {
      tState.value = 'ok'
      tMsg.value = `${d.latency_ms ?? '?'}ms · ${d.model || key}${d.reply ? ` · ${String(d.reply).slice(0, 40)}` : ''}`
    } else {
      tState.value = 'err'
      tMsg.value = d.error || '测试失败'
    }
  } catch (e) {
    tState.value = 'err'
    tMsg.value = String(e.message || e)
  }
}

async function del(m) {
  if (!confirm(`删除自定义模型「${m.name}」?`)) return
  try {
    await removeCustom(m.name)
    say('已删除')
  } catch (e) {
    say(`删除失败:${e.message}`)
  }
}

function isCur(source, key) {
  const c = model.current
  if (!c) return source === 'auto'
  if (c.source !== source) return false
  return source === 'preset' ? c.provider === key : c.name === key
}
</script>

<template>
  <div ref="root" class="mp">
    <button class="pill" :class="{ on: open }" :title="`当前模型:${cur.sub}`" @click="toggle">
      <span class="dot" :style="{ background: cur.color, boxShadow: `0 0 6px ${cur.color}` }" />
      <span class="nm">{{ cur.name }}</span>
      <span class="caret">▾</span>
    </button>

    <div v-if="open" class="pop">
      <template v-if="!form">
        <button class="it" :class="{ on: isCur('auto') }" @click="pick({ source: 'auto' })">
          <span class="dot" style="background: #7e93b8" />
          <span class="tx"><b>Auto</b><span>跟随 .env 默认</span></span>
          <span v-if="isCur('auto')" class="tg">当前</span>
        </button>

        <div class="sep">预置</div>
        <div v-if="model.state === 'loading'" class="empty">加载中…</div>
        <div v-else-if="model.state === 'error'" class="empty">读取失败:{{ model.err }}</div>
        <div
          v-for="p in model.providers"
          v-else
          :key="p.provider"
          class="it"
          :class="{ on: isCur('preset', p.provider), dis: !p.configured }"
        >
          <span class="dot" :style="{ background: p.color, boxShadow: `0 0 6px ${p.color}` }" />
          <button
            class="tx plain"
            @click="p.configured ? pick({ source: 'preset', provider: p.provider }) : say(`${p.provider} 未在 .env 配置 Key`)"
          >
            <b>{{ p.label }}</b><span>{{ p.provider }}</span>
          </button>
          <span v-if="isCur('preset', p.provider)" class="tg">当前</span>
          <span v-else-if="!p.configured" class="tg warn">未配置</span>
          <button
            class="x"
            :disabled="!p.configured || tState === 'busy'"
            title="连通性测试"
            @click.stop="test('preset', p.provider)"
          >
            {{ tState === 'busy' && tKey === `preset:${p.provider}` ? '…' : '测' }}
          </button>
        </div>

        <template v-if="model.custom.length">
          <div class="sep">自定义</div>
          <div v-for="m in model.custom" :key="m.name" class="it" :class="{ on: isCur('custom', m.name) }">
            <span class="dot" style="background: #a78bfa; box-shadow: 0 0 6px #a78bfa" />
            <button class="tx plain" @click="pick({ source: 'custom', name: m.name })">
              <b>{{ m.name }}</b>
              <span>{{ m.model }}<template v-if="m.note"> · {{ m.note }}</template></span>
            </button>
            <span v-if="isCur('custom', m.name)" class="tg">当前</span>
            <button
              class="x"
              :disabled="tState === 'busy'"
              title="连通性测试"
              @click.stop="test('custom', m.name)"
            >
              {{ tState === 'busy' && tKey === `custom:${m.name}` ? '…' : '测' }}
            </button>
            <button class="x" title="编辑" @click.stop="startEdit(m)">编</button>
            <span class="x" title="删除" @click="del(m)">✕</span>
          </div>
        </template>

        <div v-if="tState === 'busy'" class="tres">测试中…</div>
        <div v-else-if="tMsg" class="tres" :class="tState">
          {{ tState === 'ok' ? '✓' : '✕' }} {{ tMsg }}
        </div>

        <div class="foot">
          <button class="fbtn" @click="startAdd">＋ 添加自定义模型</button>
          <button class="fbtn" title="重新读取" @click="loadModels(true)">⟳</button>
        </div>
      </template>

      <form v-else class="form" @submit.prevent="submit">
        <div class="fhead">
          {{ form.edit ? '编辑自定义模型' : '添加自定义模型' }}
          <span class="x" @click="form = null">✕</span>
        </div>
        <label>名称
          <input v-model="form.name" :disabled="form.edit" placeholder="我的 DeepSeek" />
        </label>
        <label>Base URL<input v-model="form.base_url" placeholder="https://api.deepseek.com/v1" /></label>
        <label>API Key
          <input
            v-model="form.api_key"
            type="password"
            :placeholder="form.edit ? (form.has_key ? '留空 = 保留原 Key' : '尚未设置,请填写') : 'sk-…'"
          />
        </label>
        <label>模型 ID<input v-model="form.model" placeholder="deepseek-chat" /></label>
        <label>备注<input v-model="form.note" placeholder="可选:这个模型用在哪 / 限速多少" /></label>
        <button class="submit" :disabled="busy">
          {{ busy ? '保存中…' : (form.edit ? '保存修改' : '保存并保持未选中') }}
        </button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.mp { position: relative; flex-shrink: 0; }

.pill {
  display: flex; align-items: center; gap: 5px;
  padding: 5px 9px; border-radius: var(--r-pill);
  font-size: 12px; font-weight: 600; color: var(--ink);
  background: var(--surface-2);
  max-width: 150px;
  transition: background 0.15s, color 0.15s;
}
.pill:hover, .pill.on { background: var(--surface-3); color: var(--ink); }
.pill .dot { width: 5px; height: 5px; border-radius: 50%; flex-shrink: 0; }
.pill .nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pill .caret { font-size: 10px; color: var(--dim); }

.pop {
  position: absolute; left: 0; bottom: calc(100% + 8px);
  z-index: var(--z-menu);
  width: 272px; max-height: 54vh;
  display: flex; flex-direction: column;
  padding: 6px;
  border-radius: var(--r-mid);
  background: rgba(255, 255, 255, 0.92);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  backdrop-filter: blur(24px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.9);
  box-shadow: var(--sh-pop);
  overflow-y: auto;
  animation: mp-in 0.15s ease;
}
@keyframes mp-in { from { opacity: 0; transform: translateY(5px); } }

.it {
  width: 100%;
  display: flex; align-items: center; gap: 8px;
  padding: 6px 7px; border-radius: var(--r-sm);
  text-align: left;
  transition: background 0.13s;
}
.it:hover { background: var(--surface-2); }
.it.on { background: var(--accent-soft); }
.it.dis { opacity: 0.55; }
.it .dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.it .tx { display: flex; flex-direction: column; gap: 1px; flex: 1; min-width: 0; }
.it .tx b { font-size: 13px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.it .tx span { font-size: 11px; color: var(--dim); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.it .tx.plain { background: none; padding: 0; }
.tg {
  flex-shrink: 0; font-size: 10.5px; font-weight: 600; padding: 2px 6px; border-radius: var(--r-pill);
  color: var(--accent); background: rgba(26, 157, 95, 0.13);
}
.tg.warn { color: var(--amber); background: rgba(200, 140, 40, 0.13); }
.it .x { flex-shrink: 0; font-size: 11px; color: var(--dim); padding: 2px 4px; }
.it .x:hover { color: var(--red); }
/* 测 / 编 是 button,去掉按钮底框,和 ✕ 同一个观感 */
button.x { background: none; border: 0; font-family: inherit; cursor: pointer; }
button.x:hover:not(:disabled) { color: var(--accent); }
button.x:disabled { opacity: 0.4; cursor: default; }

/* 连通性测试结果条:失败原因原样显示(常见是 key 错 / base_url 多一层 /v1) */
.tres {
  margin-top: 5px; padding: 7px 9px; border-radius: var(--r-sm);
  font-size: 11.5px; line-height: 1.55; color: var(--dim);
  background: var(--surface-2);
  overflow-wrap: anywhere;
}
.tres.ok { color: var(--green); background: rgba(26, 157, 95, 0.08); }
.tres.err { color: var(--red); background: rgba(229, 72, 77, 0.07); }

.sep {
  padding: 9px 7px 4px;
  font-size: 11px; font-weight: 700; letter-spacing: 0.08em;
  color: var(--dim); text-transform: uppercase;
}
.empty { padding: 10px 7px; font-size: 12px; color: var(--dim); }

/* 列表可能长到滚出视口,底部两个按钮必须常驻可点 */
.foot {
  position: sticky; bottom: -6px;
  display: flex; gap: 5px;
  padding: 6px 4px 4px; margin-top: 5px;
  border-top: 1px solid var(--line-soft);
  background: rgba(255, 255, 255, 0.96);
  -webkit-backdrop-filter: blur(10px);
  backdrop-filter: blur(10px);
}
.fbtn {
  flex: 1; padding: 7px; border-radius: var(--r-sm);
  font-size: 12px; font-weight: 600; color: var(--dim);
  background: var(--surface-2);
}
.fbtn:hover { color: var(--ink); background: var(--surface-3); }

.form { display: flex; flex-direction: column; gap: 6px; padding: 4px; }
.fhead {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 12.5px; font-weight: 700; padding: 2px 3px 4px;
}
.fhead .x { cursor: pointer; color: var(--dim); font-size: 12px; }
.form label { display: flex; flex-direction: column; gap: 3px; font-size: 11.5px; font-weight: 600; color: var(--dim); }
.form input {
  padding: 6px 8px; border-radius: 7px; font-size: 12px;
  background: #fff; border: 1px solid var(--line);
}
.form input:focus { border-color: rgba(26, 157, 95, 0.45); }
.form input:disabled { color: var(--faint); background: var(--surface-2); }
.submit {
  margin-top: 3px; padding: 7px; border-radius: var(--r-sm);
  font-size: 12px; font-weight: 600; color: #fff; background: var(--green);
}
.submit:disabled { opacity: 0.6; }
</style>
