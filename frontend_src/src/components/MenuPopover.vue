<script setup>
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { MODULES } from '../stores/app.js'

const props = defineProps({
  anchor: { type: Object, default: null },
  active: { type: String, default: 'overview' },
})
const emit = defineEmits(['pick', 'close'])

/* 按 group 切块,保留「概览 / 工作台 / 能力 / 知识」的分段节奏 */
const groups = computed(() => {
  const out = []
  let cur = null
  for (const m of MODULES) {
    if (!m.group) {
      out.push({ title: '', items: [m] })
      continue
    }
    if (!cur || cur.title !== m.group) {
      cur = { title: m.group, items: [] }
      out.push(cur)
    }
    cur.items.push(m)
  }
  return out
})

const style = computed(() => {
  const a = props.anchor || { left: 16, top: 700 }
  return { left: `${a.left}px`, bottom: `${window.innerHeight - a.top + 8}px` }
})

function onDocDown(e) {
  if (e.target.closest('.nav-pop') || e.target.closest('.menu-btn')) return
  emit('close')
}
function onKey(e) {
  if (e.key === 'Escape') emit('close')
}

onMounted(() => {
  document.addEventListener('mousedown', onDocDown, true)
  document.addEventListener('keydown', onKey)
  window.addEventListener('resize', () => emit('close'))
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocDown, true)
  document.removeEventListener('keydown', onKey)
})
</script>

<template>
  <Teleport to="body">
    <div class="nav-pop" :style="style">
      <div class="pop-head">功能 <span class="x" title="关闭 (Esc)" @click="$emit('close')">✕</span></div>
      <div class="pop-body">
        <template v-for="(g, gi) in groups" :key="gi">
          <div v-if="g.title" class="pop-sec">{{ g.title }}</div>
          <button
            v-for="m in g.items"
            :key="m.id"
            class="pop-item"
            :class="{ on: m.id === active }"
            @click="$emit('pick', m.id)"
          >
            <span class="lbl">{{ m.label }}</span>
            <span class="tag">{{ m.tag }}</span>
          </button>
        </template>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.nav-pop {
  position: fixed;
  z-index: var(--z-menu);
  width: 214px;
  max-height: 64vh;
  display: flex;
  flex-direction: column;
  border-radius: var(--r-mid);
  background: rgba(255, 255, 255, 0.88);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  backdrop-filter: blur(24px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.9);
  box-shadow: var(--sh-pop);
  overflow: hidden;
  animation: pop-in 0.16s ease;
}
@keyframes pop-in {
  from { opacity: 0; transform: translateY(6px) scale(0.985); }
}
.pop-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 9px 11px; flex-shrink: 0;
  font-size: 11px; font-weight: 700; letter-spacing: 0.06em;
  color: var(--faint); text-transform: uppercase;
  border-bottom: 1px solid var(--line-soft);
}
.pop-head .x { cursor: pointer; font-size: 11px; color: var(--faint); padding: 2px 4px; }
.pop-head .x:hover { color: var(--ink); }
.pop-body { padding: 6px; overflow-y: auto; }
.pop-sec {
  padding: 8px 6px 4px;
  font-size: 10px; font-weight: 700; letter-spacing: 0.08em;
  color: var(--faint); text-transform: uppercase;
}
.pop-item {
  width: 100%;
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 7px 8px; border-radius: var(--r-sm);
  transition: background 0.14s;
}
.pop-item .lbl { font-size: 12.5px; font-weight: 500; }
.pop-item .tag { font-size: 10px; color: var(--faint); }
.pop-item:hover { background: var(--surface-2); }
.pop-item.on { background: var(--accent-soft); }
.pop-item.on .lbl { color: var(--accent); font-weight: 600; }
</style>
