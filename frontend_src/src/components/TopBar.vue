<script setup>
import { onMounted, ref } from 'vue'
import { getJSON } from '../api.js'

const uplink = ref('···')
const tone = ref('dim')

onMounted(async () => {
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
</script>

<template>
  <header class="topbar">
    <div class="brand">
      <div class="logo">H</div>
      <h1>Snowfoxclaw</h1>
      <span class="ver">v3.0</span>
    </div>
    <div class="spacer" />
    <div class="stat">
      <span class="lbl">系统状态</span>
      <span class="val" :class="tone">{{ uplink }}</span>
    </div>
    <button class="tbtn" title="打开 HKC 知识星球" @click="openPlanet">🌐 知识星球</button>
    <button class="tbtn" title="设置（P3 接入）">⚙</button>
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
  font-size: 10.5px; color: var(--dim);
  padding: 1px 6px; border-radius: var(--r-pill);
  background: rgba(255, 255, 255, 0.7); border: 1px solid var(--line-soft);
}
.spacer { flex: 1; }
.stat { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--dim); }
.stat .val { font-weight: 600; }
.stat .val.ok { color: var(--green); }
.stat .val.bad { color: var(--amber); }
.stat .val.dim { color: var(--faint); }
.tbtn {
  font-size: 12.5px; color: var(--dim);
  padding: 6px 10px; border-radius: var(--r-sm);
  border: 1px solid var(--line-soft); background: rgba(255, 255, 255, 0.6);
  transition: color 0.15s, background 0.15s;
}
.tbtn:hover { color: var(--ink); background: #fff; }
</style>
