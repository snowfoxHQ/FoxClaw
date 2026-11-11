<script setup>
import { computed, onMounted, ref } from 'vue'
import PanelBox from './PanelBox.vue'
import { getJSON } from '../../api.js'
import { currentLabel, loadModels, modelOptions, selectModel } from '../../stores/model.js'
import { setModule } from '../../stores/app.js'
import { setMode, chat, say } from '../../stores/chat.js'
import { profile, loadProfile, saveNickname } from '../../stores/profile.js'
import { loadSystemInfo, system } from '../../stores/system.js'
import { i18n, LOCALES, setLocale, t } from '../../lib/i18n.js'

const info = typeof __APP_INFO__ === 'undefined' ? null : __APP_INFO__
const sha = info?.git?.sha || 'dev'
const age = computed(() => {
  const ts = info?.git?.ts || 0
  if (!ts) return t('settings.buildLocal')
  const d = Math.floor((Date.now() - ts) / 86400000)
  return d <= 0 ? t('settings.buildToday') : t('settings.buildDays', { n: d })
})

const open = ref('') // '' | 'model' | 'keys' —— 一次只展开一组,行数不会失控
const up = ref(null) // null=探测中 | true | false
const err = ref('')

const cur = computed(() => currentLabel())
const options = computed(() => modelOptions())

const KEYS = computed(() => [
  ['Enter', t('key.enter')],
  ['Shift + Enter', t('key.shiftEnter')],
  ['/', t('key.slash')],
  ['@', t('key.at')],
  ['↑ ↓', t('key.arrows')],
  ['Esc', t('key.esc')],
  ['Ctrl + ,', t('key.ctrlComma')],
])

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
    say(profile.nickname ? t('settings.saidNick', { name: profile.nickname }) : t('settings.saidNickCleared'))
    open.value = ''
  } catch (e) {
    say(t('settings.errSaveNick', { msg: e.message }), 4000)
  }
}

function pickLang(id) {
  setLocale(id)
  const name = LOCALES.find((l) => l.id === id)?.label || id
  say(t('settings.saidLang', { name }))
}

const upd = computed(() => system.update || null)
const updLabel = computed(() => {
  const u = upd.value
  if (!u) return '…'
  if (!u.enabled) return t('settings.updNotEnabled')
  if (u.update_available) return t('settings.updAvailable', { v: u.latest })
  if (u.latest) return t('settings.updLatest')
  return u.error ? t('settings.updFailed') : t('settings.updPending')
})
const updHint = computed(() => {
  const u = upd.value
  if (!u) return t('common.loading')
  if (!u.enabled) return t('settings.updHintOff')
  if (u.error) return t('settings.updHintError', { msg: u.error })
  if (u.update_available) return t('settings.updHintNew', { cur: u.current, latest: u.latest })
  return t('settings.updHintOk', { cur: u.current })
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
    say(t('settings.saidSwitched', { label: o.label }))
  } catch (e) {
    err.value = e.message
    say(t('settings.errSwitch', { msg: e.message }), 4000)
  }
}

function mode(m) {
  setMode(m)
  say(m === 'stream' ? t('settings.saidStream') : t('settings.saidOrchestrate'))
}

const ICONS = {
  gear: ['M12 9.2a2.8 2.8 0 1 0 0 5.6 2.8 2.8 0 0 0 0-5.6z', 'M12 3.2v2.2', 'M12 18.6v2.2', 'M3.2 12h2.2', 'M18.6 12h2.2', 'M5.8 5.8l1.6 1.6', 'M16.6 16.6l1.6 1.6', 'M18.2 5.8l-1.6 1.6', 'M7.4 16.6l-1.6 1.6'],
  gauge: ['M3.6 15.6a8.4 8.4 0 1 1 16.8 0', 'M12 15.6l4.2-4.8'],
  chat: ['M19.6 14.8a1.8 1.8 0 0 1-1.8 1.8H8.6L5 20v-3.4H6.2A1.8 1.8 0 0 1 4.4 14.8V6.4A1.8 1.8 0 0 1 6.2 4.6h11.6a1.8 1.8 0 0 1 1.8 1.8z'],
  keys: ['M3.8 8.2h16.4v7.6H3.8z', 'M7 11h.01', 'M11 11h.01', 'M15 11h.01', 'M9 13.8h6'],
  server: ['M4.4 5.4h15.2v4.8H4.4z', 'M4.4 13.8h15.2v4.8H4.4z', 'M7.4 7.8h.01', 'M7.4 16.2h.01'],
  globe: ['M12 3.4a8.6 8.6 0 1 0 0 17.2 8.6 8.6 0 0 0 0-17.2z', 'M3.6 12h16.8', 'M12 3.4c2.2 2.4 3.4 5.4 3.4 8.6s-1.2 6.2-3.4 8.6c-2.2-2.4-3.4-5.4-3.4-8.6s1.2-6.2 3.4-8.6z'],
}

const icon = (n) => ICONS[n] || ICONS.gear
</script>

<template>
  <PanelBox :title="t('settings.title')" tag="Settings" state="idle" :ready="true" @refresh="refresh">
    <div class="card">
      <div class="grp">{{ t('settings.groupModel') }}</div>

      <button class="row" :aria-expanded="open === 'model'" @click="open = open === 'model' ? '' : 'model'">
        <span class="ic"><svg viewBox="0 0 24 24"><path v-for="(d, i) in icon('gear')" :key="i" :d="d" /></svg></span>
        <span class="m"><b>{{ t('settings.modelProvider') }}</b><span>{{ t('settings.modelProviderSub') }}</span></span>
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
          <span v-if="o.active" class="pn-tag green">{{ t('settings.current') }}</span>
          <span v-else-if="o.configured === false" class="pn-tag amber">{{ t('settings.unconfigured') }}</span>
        </button>
        <p class="tip">
          {{ t('settings.providerNote') }}
        </p>
        <p v-if="err" class="tip err">{{ err }}</p>
      </div>

      <button class="row" @click="setModule('usage')">
        <span class="ic"><svg viewBox="0 0 24 24"><path v-for="(d, i) in icon('gauge')" :key="i" :d="d" /></svg></span>
        <span class="m"><b>{{ t('settings.usage') }}</b><span>{{ t('settings.usageSub') }}</span></span>
        <span class="chev">›</span>
      </button>
    </div>

    <div class="card">
      <div class="grp">{{ t('settings.groupChat') }}</div>

      <div class="row static">
        <span class="ic"><svg viewBox="0 0 24 24"><path v-for="(d, i) in icon('chat')" :key="i" :d="d" /></svg></span>
        <span class="m"><b>{{ t('settings.mode') }}</b><span>{{ t('settings.modeSub') }}</span></span>
        <span class="seg">
          <button :class="{ on: chat.mode === 'stream' }" @click="mode('stream')">{{ t('settings.stream') }}</button>
          <button :class="{ on: chat.mode === 'orchestrate' }" @click="mode('orchestrate')">{{ t('settings.orchestrate') }}</button>
        </span>
      </div>

      <button class="row" :aria-expanded="open === 'nick'" @click="open = open === 'nick' ? '' : 'nick'">
        <span class="ic"><svg viewBox="0 0 24 24"><path v-for="(d, i) in icon('chat')" :key="i" :d="d" /></svg></span>
        <span class="m"><b>{{ t('settings.nickname') }}</b><span>{{ t('settings.nicknameSub') }}</span></span>
        <span class="hint">{{ profile.nickname || t('common.notSet') }}</span>
        <span class="chev" :class="{ open: open === 'nick' }">›</span>
      </button>

      <div v-if="open === 'nick'" class="sub">
        <div class="nick-edit">
          <input
            v-model="nickDraft"
            class="nick-in"
            maxlength="24"
            :placeholder="t('settings.nicknamePh')"
            @keydown.enter.prevent="saveNick"
          />
          <button class="nick-save" :disabled="profile.saving" @click="saveNick">{{ t('common.save') }}</button>
        </div>
        <p class="tip">{{ t('settings.nicknameNote') }}</p>
      </div>

      <button class="row" :aria-expanded="open === 'keys'" @click="open = open === 'keys' ? '' : 'keys'">
        <span class="ic"><svg viewBox="0 0 24 24"><path v-for="(d, i) in icon('keys')" :key="i" :d="d" /></svg></span>
        <span class="m"><b>{{ t('settings.shortcuts') }}</b><span>{{ t('settings.shortcutsSub') }}</span></span>
        <span class="hint">{{ t('common.count', { n: KEYS.length }) }}</span>
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
      <div class="grp">{{ t('settings.groupUi') }}</div>

      <div class="row static">
        <span class="ic"><svg viewBox="0 0 24 24"><path v-for="(d, i) in icon('globe')" :key="i" :d="d" /></svg></span>
        <span class="m"><b>{{ t('settings.language') }}</b><span>{{ t('settings.languageSub') }}</span></span>
        <span class="seg">
          <button
            v-for="l in LOCALES"
            :key="l.id"
            :class="{ on: i18n.locale === l.id }"
            @click="pickLang(l.id)"
          >{{ l.label }}</button>
        </span>
      </div>

      <p class="tip">{{ t('settings.languageNote') }}</p>
    </div>

    <div class="card">
      <div class="grp">{{ t('settings.groupSystem') }}</div>

      <button class="row" :title="up ? t('settings.reprobe') : t('settings.retry')" @click="probe">
        <span class="ic"><svg viewBox="0 0 24 24"><path v-for="(d, i) in icon('server')" :key="i" :d="d" /></svg></span>
        <span class="m">
          <b>{{ t('settings.backend') }}</b>
          <span>/api · {{ up === null ? t('settings.probing') : up ? t('common.online') : t('common.offlineShort') }}</span>
        </span>
        <span class="pn-tag" :class="up ? 'green' : up === false ? 'amber' : ''">
          {{ up === null ? '…' : up ? t('settings.ok') : t('settings.retry') }}
        </span>
      </button>

      <div class="row static">
        <span class="ic"><svg viewBox="0 0 24 24"><path v-for="(d, i) in icon('gauge')" :key="i" :d="d" /></svg></span>
        <span class="m"><b>{{ t('settings.updates') }}</b><span>{{ updHint }}</span></span>
        <span class="pn-tag" :class="upd?.update_available ? 'amber' : ''">{{ updLabel }}</span>
      </div>
    </div>

    <div class="foot">
      <span class="mono">git@{{ sha }} · {{ age }}</span>
      <span class="dev" :title="t('settings.buildLocal')">▭</span>
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
