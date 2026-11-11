<script setup>
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { groupLabel, moduleLabel, moreItems } from '../stores/app.js'
import { t } from '../lib/i18n.js'

const props = defineProps({
  anchor: { type: Object, default: null },
  active: { type: String, default: 'overview' },
})
const emit = defineEmits(['pick', 'close'])

const groups = computed(() => {
  const out = []
  let cur = null
  for (const m of moreItems()) {
    if (!m.groupKey) {
      out.push({ titleKey: '', items: [m] })
      continue
    }
    if (!cur || cur.titleKey !== m.groupKey) {
      cur = { titleKey: m.groupKey, items: [] }
      out.push(cur)
    }
    cur.items.push(m)
  }
  return out
})

const style = computed(() => {
  const a = props.anchor
  if (!a) return { left: '16px', top: '120px' }
  const top = (a.bottom ?? 0) + 6
  return { left: `${a.left}px`, top: `${top}px` }
})

function onDocDown(e) {
  if (e.target.closest('.nav-pop') || e.target.closest('.rail-anchor')) return
  emit('close')
}
function onKey(e) {
  if (e.key === 'Escape') emit('close')
}
function onResize() {
  emit('close')
}

onMounted(() => {
  document.addEventListener('mousedown', onDocDown, true)
  document.addEventListener('keydown', onKey)
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocDown, true)
  document.removeEventListener('keydown', onKey)
  window.removeEventListener('resize', onResize)
})
</script>

<template>
  <Teleport to="body">
    <div class="nav-pop" :style="style">
      <div class="pop-head">{{ t('menu.title') }} <span class="x" :title="t('menu.close')" @click="$emit('close')">✕</span></div>
      <div class="pop-body">
        <template v-for="(g, gi) in groups" :key="gi">
          <div v-if="g.titleKey" class="pop-sec">{{ groupLabel(g.titleKey) }}</div>
          <button
            v-for="m in g.items"
            :key="m.id"
            class="pop-item"
            :class="{ on: m.id === active }"
            @click="$emit('pick', m.id)"
          >
            <span class="lbl">{{ moduleLabel(m.id) }}</span>
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
  max-height: min(64vh, 420px);
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
  from { opacity: 0; transform: translateY(-6px) scale(0.985); }
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
  padding: 9px 6px 5px;
  font-size: 11px; font-weight: 700; letter-spacing: 0.08em;
  color: var(--dim); text-transform: uppercase;
}
.pop-item {
  width: 100%;
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 8px 9px; border-radius: var(--r-sm);
  transition: background 0.14s;
}
.pop-item .lbl { font-size: 13px; font-weight: 600; color: var(--ink); }
.pop-item .tag { font-size: 11px; font-weight: 600; color: var(--dim); }
.pop-item:hover { background: var(--surface-2); }
.pop-item.on { background: var(--accent-soft); }
.pop-item.on .lbl { color: var(--accent); font-weight: 600; }
</style>
