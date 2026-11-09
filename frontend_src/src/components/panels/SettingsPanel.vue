<script setup>
import { computed, onMounted, ref } from 'vue'
import PanelBox from './PanelBox.vue'
import { getJSON } from '../../api.js'
import { currentLabel, loadModels, modelOptions, selectModel } from '../../stores/model.js'
import { setModule } from '../../stores/app.js'
import { setMode, chat, say } from '../../stores/chat.js'
import { profile, loadProfile, saveNickname } from '../../stores/profile.js'
import { loadSystemInfo, system } from '../../stores/system.js'

/* __APP_INFO__ 由 vite define 在构建时注入 { git:{sha,ts}, built };没有 git 时是 dev。
   用 typeof 兜一层:万一在没走过 vite 的环境里被单独加载,不要抛 ReferenceError。 */
const info = typeof __APP_INFO__ === 'undefined' ? null : __APP_INFO__
const sha = info?.git?.sha || 'dev'
const age = (() => {
  const ts = info?.git?.ts || 0
  if (!ts) return '本地构建'
  const d = Math.floor((Date.now() - ts) / 86400000)
  return d <= 0 ? '今天' : `${d} 天前`
})()

const open = ref('') // '' | 'model' | 'keys' —— 一次只展开一组,行数不会失控
const up = ref(null) // null=探测中 | true | false
const err = ref('')

const cur = computed(() => currentLabel())
const options = computed(() => modelOptions())

/* 只列真正存在的键位。ChatDock.onKey 里没有 Ctrl+Enter 收录、没有 / 之外的快捷键,
   这里就不写 —— 设置里写一条按不出来的键,比不写更糟。 */
const KEYS = [
  ['Enter', '发送'],
  ['Shift + Enter', '换行'],
  ['/', '命令补全(如 /搜索)'],
  ['@', '技能补全'],
  ['↑ ↓', '在补全列表里选'],
  ['Esc', '关闭补全 / 浮层'],
  ['Ctrl + ,', '打开设置'],
]

/* ── 称呼 ── */
const nickDraft = ref('')

onMounted(() => {
  loadModels()
  loadSystemInfo()
  loadProfile().then(() => { nickDraft.value = profile.nickname })
  probe()
})

async function saveNick() {
  try {
    await saveNickname(nickDraft.value)
    say(profile.nickname ? `以后称呼你为 ${profile.nickname}` : '已清除称呼')
    open.value = ''
  } catch (e) {
    say(`保存失败:${e.message}`, 4000)
  }
}

/* ── 新版本提示 ──
   后端默认不查(FOXCLAW_UPDATE_CHECK=1 才查),这里如实显示「未开启」,
   不诱导用户打开,也不假装已经是最新。 */
const upd = computed(() => system.update || null)
const updLabel = computed(() => {
  const u = upd.value
  if (!u) return '…'
  if (!u.enabled) return '未开启'
  if (u.update_available) return `新版本 ${u.latest}`
  if (u.latest) return '已是最新'
  return u.error ? '检查失败' : '待检查'
})
const updHint = computed(() => {
  const u = upd.value
  if (!u) return '读取中…'
  if (!u.enabled) return '默认关闭 · 设 FOXCLAW_UPDATE_CHECK=1 开启'
  if (u.error) return `检查失败(不影响使用):${u.error}`
  if (u.update_available) return `当前 v${u.current} · 最新 v${u.latest}`
  return `当前 v${u.current} · 24 小时查一次`
})

async function probe() {
  up.value = null
  try {
    await getJSON('/status')
    up.value = true
  } catch {
    up.value = false
  }
}

async function refresh() {
  await loadModels(true).catch(() => {})
  loadSystemInfo(true)      // 更新状态也刷一次(后端 24h 缓存内不会真的外呼)
  probe()
}

async function pick(o) {
  try {
    await selectModel({
      source: o.source,
      provider: o.source === 'preset' ? o.key : null,
      name: o.source === 'custom' ? o.key : null,
    })
    open.value = ''
    say(`模型已切到 ${o.label}`)
  } catch (e) {
    err.value = e.message
    say(`切换失败:${e.message}`, 4000)
  }
}

function mode(m) {
  setMode(m)
  say(m === 'stream' ? '流式:逐字输出' : '编排:先出计划再逐步执行')
}

const ICONS = {
  gear: ['M12 9.2a2.8 2.8 0 1 0 0 5.6 2.8 2.8 0 0 0 0-5.6z', 'M12 3.2v2.2', 'M12 18.6v2.2', 'M3.2 12h2.2', 'M18.6 12h2.2', 'M5.8 5.8l1.6 1.6', 'M16.6 16.6l1.6 1.6', 'M18.2 5.8l-1.6 1.6', 'M7.4 16.6l-1.6 1.6'],
  gauge: ['M3.6 15.6a8.4 8.4 0 1 1 16.8 0', 'M12 15.6l4.2-4.8'],
  chat: ['M19.6 14.8a1.8 1.8 0 0 1-1.8 1.8H8.6L5 20v-3.4H6.2A1.8 1.8 0 0 1 4.4 14.8V6.4A1.8 1.8 0 0 1 6.2 4.6h11.6a1.8 1.8 0 0 1 1.8 1.8z'],
  keys: ['M3.8 8.2h16.4v7.6H3.8z', 'M7 11h.01', 'M11 11h.01', 'M15 11h.01', 'M9 13.8h6'],
  server: ['M4.4 5.4h15.2v4.8H4.4z', 'M4.4 13.8h15.2v4.8H4.4z', 'M7.4 7.8h.01', 'M7.4 16.2h.01'],
}

const icon = (n) => ICONS[n] || ICONS.gear
</script>

<template>
  <PanelBox title="设置" tag="Settings" state="idle" :ready="true" @refresh="refresh">
    <div class="card">
      <div class="grp">模型</div>

      <button class="row" :aria-expanded="open === 'model'" @click="open = open === 'model' ? '' : 'model'">
        <span class="ic"><svg viewBox="0 0 24 24"><path v-for="(d, i) in icon('gear')" :key="i" :d="d" /></svg></span>
        <span class="m"><b>模型与供应商</b><span>切换后服务端立即生效,不用重启</span></span>
        <span class="hint"><i class="dot" :style="{ background: cur.color }" />{{ cur.name }}</span>
        <span class="chev" :class="{ open: open === 'model' }">›</span>
      </button>

      <div v-if="open === 'model'" class="sub">
        <button
          v-for="o in options"
          :key="`${o.source}-${o.key}`"
          class="opt"
          :class="{ on: o.active }"
          @click="pick(o)"
        >
          <span class="dot" :style="{ background: o.color }" />
          <span class="m"><b>{{ o.label }}</b><span>{{ o.sub }}</span></span>
          <span v-if="o.active" class="pn-tag green">当前</span>
          <span v-else-if="o.configured === false" class="pn-tag amber">未配置</span>
        </button>
        <p class="tip">
          「未配置」= 后端缺该厂商的 Key。自定义模型的 Key / Base URL 在输入框左侧的模型胶囊里加。
        </p>
        <p v-if="err" class="tip err">{{ err }}</p>
      </div>

      <button class="row" @click="setModule('usage')">
        <span class="ic"><svg viewBox="0 0 24 24"><path v-for="(d, i) in icon('gauge')" :key="i" :d="d" /></svg></span>
        <span class="m"><b>使用情况</b><span>token、调用次数、余额</span></span>
        <span class="chev">›</span>
      </button>
    </div>

    <div class="card">
      <div class="grp">对话</div>

      <div class="row static">
        <span class="ic"><svg viewBox="0 0 24 24"><path v-for="(d, i) in icon('chat')" :key="i" :d="d" /></svg></span>
        <span class="m"><b>模式</b><span>逐字流式 / 先出计划再逐步执行</span></span>
        <span class="seg">
          <button :class="{ on: chat.mode === 'stream' }" @click="mode('stream')">流式</button>
          <button :class="{ on: chat.mode === 'orchestrate' }" @click="mode('orchestrate')">编排</button>
        </span>
      </div>

      <button class="row" :aria-expanded="open === 'nick'" @click="open = open === 'nick' ? '' : 'nick'">
        <span class="ic"><svg viewBox="0 0 24 24"><path v-for="(d, i) in icon('chat')" :key="i" :d="d" /></svg></span>
        <span class="m"><b>称呼</b><span>模型会这么叫你</span></span>
        <span class="hint">{{ profile.nickname || '未设置' }}</span>
        <span class="chev" :class="{ open: open === 'nick' }">›</span>
      </button>

      <div v-if="open === 'nick'" class="sub">
        <div class="nick-edit">
          <input
            v-model="nickDraft"
            class="nick-in"
            maxlength="24"
            placeholder="例如:snow"
            @keydown.enter.prevent="saveNick"
          />
          <button class="nick-save" :disabled="profile.saving" @click="saveNick">保存</button>
        </div>
        <p class="tip">存成一条偏好,每轮对话都会带上;留空保存 = 清除。</p>
      </div>

      <button class="row" :aria-expanded="open === 'keys'" @click="open = open === 'keys' ? '' : 'keys'">
        <span class="ic"><svg viewBox="0 0 24 24"><path v-for="(d, i) in icon('keys')" :key="i" :d="d" /></svg></span>
        <span class="m"><b>快捷键</b><span>输入框里的键位</span></span>
        <span class="hint">{{ KEYS.length }} 条</span>
        <span class="chev" :class="{ open: open === 'keys' }">›</span>
      </button>

      <div v-if="open === 'keys'" class="sub">
        <div v-for="k in KEYS" :key="k[0]" class="kv">
          <kbd>{{ k[0] }}</kbd>
          <span>{{ k[1] }}</span>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="grp">系统</div>

      <button class="row" :title="up ? '再探一次' : '重试'" @click="probe">
        <span class="ic"><svg viewBox="0 0 24 24"><path v-for="(d, i) in icon('server')" :key="i" :d="d" /></svg></span>
        <span class="m"><b>后端连接</b><span>/api · {{ up === null ? '探测中…' : up ? '在线' : '离线' }}</span></span>
        <span class="pn-tag" :class="up ? 'green' : up === false ? 'amber' : ''">
          {{ up === null ? '…' : up ? '正常' : '重试' }}
        </span>
      </button>

      <div class="row static">
        <span class="ic"><svg viewBox="0 0 24 24"><path v-for="(d, i) in icon('gauge')" :key="i" :d="d" /></svg></span>
        <span class="m"><b>新版本</b><span>{{ updHint }}</span></span>
        <span class="pn-tag" :class="upd?.update_available ? 'amber' : ''">{{ updLabel }}</span>
      </div>
    </div>

    <div class="foot">
      <span class="mono">git@{{ sha }} · {{ age }}</span>
      <span class="dev" title="本地构建">▭</span>
    </div>
  </PanelBox>
</template>

<style scoped>
.card {
  border-radius: var(--r-mid);
  background: rgba(255, 255, 255, 0.82);
  border: 1px solid var(--line-soft);
  box-shadow: 0 1px 2px rgba(23, 32, 26, 0.04);
  padding: 4px 6px 6px;
  margin-bottom: 12px;
}
.grp {
  padding: 8px 8px 6px;
  font-size: 11px; font-weight: 700; letter-spacing: 0.08em;
  color: var(--dim); text-transform: uppercase;
}

.row {
  width: 100%;
  display: flex; align-items: center; gap: 10px;
  padding: 8px; border-radius: var(--r-sm);
  text-align: left;
  transition: background 0.14s;
}
.row:hover { background: rgba(255, 255, 255, 0.9); }
.row.static:hover { background: transparent; }
.row .ic { width: 22px; height: 22px; display: grid; place-items: center; color: var(--dim); flex-shrink: 0; }
.row .ic svg {
  width: 16px; height: 16px;
  fill: none; stroke: currentColor; stroke-width: 1.6;
  stroke-linecap: round; stroke-linejoin: round;
}
.row .m { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.row .m b { font-size: 13px; font-weight: 600; color: var(--ink); }
.row .m span { font-size: 11.5px; color: var(--dim); }
.hint { display: flex; align-items: center; gap: 5px; font-size: 12px; font-weight: 600; color: var(--dim); flex-shrink: 0; }
.hint .dot { width: 7px; height: 7px; border-radius: 50%; }
.chev { color: var(--dim); font-size: 13px; flex-shrink: 0; transition: transform 0.16s; }
.chev.open { transform: rotate(90deg); }

.seg { display: flex; gap: 2px; padding: 2px; border-radius: var(--r-sm); background: rgba(23, 32, 26, 0.05); flex-shrink: 0; }
.seg button { padding: 4px 10px; border-radius: 7px; font-size: 11.5px; color: var(--dim); transition: background 0.15s, color 0.15s; }
.seg button.on { background: #fff; color: var(--ink); font-weight: 600; box-shadow: 0 1px 2px rgba(23, 32, 26, 0.07); }

.sub {
  margin: 2px 8px 6px 40px;
  padding: 4px;
  border-left: 1px solid var(--line-soft);
}
.opt {
  width: 100%;
  display: flex; align-items: center; gap: 8px;
  padding: 6px 8px; border-radius: 7px;
  text-align: left;
  transition: background 0.13s;
}
.opt:hover { background: rgba(255, 255, 255, 0.9); }
.opt.on { background: rgba(26, 157, 95, 0.09); }
.opt .dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.opt .m { flex: 1; min-width: 0; display: flex; align-items: baseline; gap: 7px; }
.opt .m b { font-size: 12.5px; font-weight: 600; color: var(--ink); }
.opt .m span { font-size: 11.5px; color: var(--dim); font-family: var(--mono); }

.tip { padding: 4px 8px 2px; font-size: 11.5px; line-height: 1.6; color: var(--dim); }
.tip.err { color: var(--red); }

.nick-edit { display: flex; align-items: center; gap: 6px; padding: 4px 8px 2px; }
.nick-in {
  flex: 1; min-width: 0;
  padding: 5px 8px; border-radius: 7px;
  font-size: 12.5px; color: var(--ink);
  background: #fff; border: 1px solid var(--line);
}
.nick-in:focus { outline: none; border-color: rgba(26, 157, 95, 0.5); }
.nick-save {
  flex-shrink: 0;
  padding: 5px 11px; border-radius: 7px;
  font-size: 12px; font-weight: 600; color: #fff;
  background: var(--green);
}
.nick-save:disabled { opacity: 0.45; }

.kv { display: flex; align-items: center; gap: 10px; padding: 4px 8px; }
.kv kbd {
  min-width: 92px; flex-shrink: 0;
  font-family: var(--mono); font-size: 11.5px; font-weight: 600; color: var(--ink);
  padding: 2px 6px; border-radius: 5px;
  background: rgba(23, 32, 26, 0.05);
}
.kv span { font-size: 11.5px; color: var(--dim); }

.foot {
  display: flex; align-items: center; justify-content: space-between;
  margin-top: 2px; padding: 10px 4px 2px;
  border-top: 1px solid var(--line-soft);
}
.foot .mono { font-family: var(--mono); font-size: 11.5px; color: var(--dim); }
.foot .dev { font-size: 12px; color: var(--faint); }
</style>
