<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJSON } from '../api.js'
import { state, setModule } from '../stores/app.js'
import { system, loadSystemInfo } from '../stores/system.js'

const uplink = ref('···')
const tone = ref('dim')

const ver = computed(() => system.version)

const upd = computed(() => (system.update?.update_available ? system.update : null))

onMounted(async () => {
  loadSystemInfo()
  try {
    await getJSON('/status')
    uplink.value = '在线'
    tone.value = 'ok'
  } catch {
    uplink.value = '离线'
    tone.value = 'bad'
  }
})

function openPlanet() {
  window.open('/planet/', '_blank', 'noopener')
}

function openSettings() {
  setModule(state.module === 'settings' ? 'overview' : 'settings')
}
</script>

<template>
  <header class="topbar">
    <div class="brand">
      <div class="logo">F</div>
      <h1>Foxclaw</h1>
      <span v-if="ver" class="ver">v{{ ver }}</span>
      <button
        v-if="upd"
        class="upd"
        :title="`有新版本 ${upd.latest}(当前 ${upd.current}),点这里看设置`"
        @click="openSettings"
      >↑</button>
    </div>
    <div class="spacer" />
    <div class="stat">
      <span class="lbl">系统状态</span>
      <span class="val" :class="tone">{{ uplink }}</span>
    </div>
    <button class="tbtn" title="打开 HKC 知识星球" @click="openPlanet">🌐 知识星球</button>
    <button
      class="tbtn"
      :class="{ on: state.module === 'settings' }"
      title="设置 (Ctrl+,)"
      @click="openSettings"
    >⚙</button>
  </header>
</template>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 16px;
  background: rgba(255, 255, 255, 0.5);
  -webkit-backdrop-filter: blur(18px) saturate(160%);
  backdrop-filter: blur(18px) saturate(160%);
  border-bottom: 1px solid var(--line-soft);
}
.brand { display: flex; align-items: center; gap: 9px; }
.logo {
  width: 28px; height: 28px; border-radius: 9px;
  display: grid; place-items: center;
  background: var(--green); color: #fff; font-weight: 800; font-size: 15px;
  box-shadow: 0 4px 12px rgba(26, 157, 95, 0.32);
}
.brand h1 { font-size: 15px; font-weight: 700; letter-spacing: -0.01em; }
.ver {
  font-size: 11px; font-weight: 600; color: var(--dim);
  padding: 2px 7px; border-radius: var(--r-pill);
  background: rgba(255, 255, 255, 0.7); border: 1px solid var(--line);
}
.upd {
  display: grid; place-items: center;
  width: 18px; height: 18px; margin-left: -3px;
  border-radius: 50%;
  font-size: 11px; font-weight: 700; line-height: 1;
  color: var(--amber);
  background: rgba(214, 158, 46, 0.12);
  border: 1px solid rgba(214, 158, 46, 0.35);
}
.upd:hover { background: rgba(214, 158, 46, 0.2); }
.spacer { flex: 1; }
.stat { display: flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--dim); }
.stat .val { font-weight: 700; color: var(--ink); }
.stat .val.ok { color: var(--green); }
.stat .val.bad { color: var(--amber); }
.stat .val.dim { color: var(--dim); }
.tbtn {
  font-size: 12.5px; font-weight: 600; color: var(--ink);
  padding: 6px 11px; border-radius: var(--r-sm);
  border: 1px solid var(--line); background: rgba(255, 255, 255, 0.7);
  transition: color 0.15s, background 0.15s;
}
.tbtn:hover { color: var(--ink); background: #fff; }
.tbtn.on { color: var(--green); background: #fff; border-color: rgba(26, 157, 95, 0.32); }
</style>
