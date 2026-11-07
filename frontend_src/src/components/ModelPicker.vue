<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { addCustom, currentLabel, loadModels, model, removeCustom, selectModel } from '../stores/model.js'
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
  form.value = { name: '', base_url: '', api_key: '', model: '', note: '' }
}

async function submitAdd() {
  const f = form.value
  if (!f.name.trim() || !f.base_url.trim() || !f.api_key.trim() || !f.model.trim()) {
    return say('名称 / Base URL / Key / 模型 ID 都必填')
  }
  busy.value = true
  try {
    await addCustom({ ...f, name: f.name.trim() })
    form.value = null
    say('已添加自定义模型')
  } catch (e) {
    say(`添加失败:${e.message}`)
  } finally {
    busy.value = false
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
        <button
          v-for="p in model.providers"
          v-else
          :key="p.provider"
          class="it"
          :class="{ on: isCur('preset', p.provider), dis: !p.configured }"
          @click="p.configured ? pick({ source: 'preset', provider: p.provider }) : say(`${p.provider} 未在 .env 配置 Key`)"
        >
          <span class="dot" :style="{ background: p.color, boxShadow: `0 0 6px ${p.color}` }" />
          <span class="tx"><b>{{ p.label }}</b><span>{{ p.provider }}</span></span>
          <span v-if="isCur('preset', p.provider)" class="tg">当前</span>
          <span v-else-if="!p.configured" class="tg warn">未配置</span>
        </button>

        <template v-if="model.custom.length">
          <div class="sep">自定义</div>
          <div v-for="m in model.custom" :key="m.name" class="it" :class="{ on: isCur('custom', m.name) }">
            <span class="dot" style="background: #a78bfa; box-shadow: 0 0 6px #a78bfa" />
            <button class="tx plain" @click="pick({ source: 'custom', name: m.name })">
              <b>{{ m.name }}</b><span>{{ m.model }}</span>
            </button>
            <span v-if="isCur('custom', m.name)" class="tg">当前</span>
            <span class="x" title="删除" @click="del(m)">✕</span>
          </div>
        </template>

        <div class="foot">
          <button class="fbtn" @click="startAdd">＋ 添加自定义模型</button>
          <button class="fbtn" title="重新读取" @click="loadModels(true)">⟳</button>
        </div>
      </template>

      <form v-else class="form" @submit.prevent="submitAdd">
        <div class="fhead">添加自定义模型 <span class="x" @click="form = null">✕</span></div>
        <label>名称<input v-model="form.name" placeholder="我的 DeepSeek" /></label>
        <label>Base URL<input v-model="form.base_url" placeholder="https://api.deepseek.com/v1" /></label>
        <label>API Key<input v-model="form.api_key" type="password" placeholder="sk-…" /></label>
        <label>模型 ID<input v-model="form.model" placeholder="deepseek-chat" /></label>
        <button class="submit" :disabled="busy">{{ busy ? '保存中…' : '保存并保持未选中' }}</button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.mp { position: relative; flex-shrink: 0; }

.pill {
  display: flex; align-items: center; gap: 5px;
  padding: 5px 9px; border-radius: var(--r-pill);
  font-size: 11.5px; color: var(--dim);
  background: var(--surface-2);
  max-width: 150px;
  transition: background 0.15s, color 0.15s;
}
.pill:hover, .pill.on { background: var(--surface-3); color: var(--ink); }
.pill .dot { width: 5px; height: 5px; border-radius: 50%; flex-shrink: 0; }
.pill .nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pill .caret { font-size: 8px; color: var(--faint); }

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
.it .tx b { font-size: 12.5px; font-weight: 550; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.it .tx span { font-size: 10px; color: var(--faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.it .tx.plain { background: none; padding: 0; }
.tg {
  flex-shrink: 0; font-size: 9.5px; padding: 1px 5px; border-radius: var(--r-pill);
  color: var(--accent); background: rgba(26, 157, 95, 0.13);
}
.tg.warn { color: var(--amber); background: rgba(200, 140, 40, 0.13); }
.it .x { flex-shrink: 0; font-size: 10px; color: var(--faint); padding: 2px 4px; }
.it .x:hover { color: var(--red); }

.sep {
  padding: 8px 7px 3px;
  font-size: 9.5px; font-weight: 700; letter-spacing: 0.08em;
  color: var(--faint); text-transform: uppercase;
}
.empty { padding: 10px 7px; font-size: 11.5px; color: var(--faint); }

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
  flex: 1; padding: 6px; border-radius: var(--r-sm);
  font-size: 11.5px; color: var(--dim);
  background: var(--surface-2);
}
.fbtn:hover { color: var(--ink); background: var(--surface-3); }

.form { display: flex; flex-direction: column; gap: 6px; padding: 4px; }
.fhead {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 12px; font-weight: 600; padding: 2px 3px 4px;
}
.fhead .x { cursor: pointer; color: var(--faint); font-size: 11px; }
.form label { display: flex; flex-direction: column; gap: 3px; font-size: 10.5px; color: var(--faint); }
.form input {
  padding: 6px 8px; border-radius: 7px; font-size: 12px;
  background: #fff; border: 1px solid var(--line);
}
.form input:focus { border-color: rgba(26, 157, 95, 0.45); }
.submit {
  margin-top: 3px; padding: 7px; border-radius: var(--r-sm);
  font-size: 12px; font-weight: 600; color: #fff; background: var(--green);
}
.submit:disabled { opacity: 0.6; }
</style>
