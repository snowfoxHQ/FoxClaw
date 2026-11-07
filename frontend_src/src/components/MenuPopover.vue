<script setup>
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { moreItems } from '../stores/app.js'

const props = defineProps({
  anchor: { type: Object, default: null },
  active: { type: String, default: 'overview' },
})
const emit = defineEmits(['pick', 'close'])

/* 只剩常驻入口之外的功能(概览 / 活动 / 使用情况 / 复习 / 记忆中心)。
   常驻项不在这里重复出现 —— 「更多」的语义是「别处放不下的」,不是「全部重列一遍」。 */
const groups = computed(() => {
  const out = []
  let cur = null
  for (const m of moreItems()) {
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

/* 入口在栏顶 → 浮层向下展开。left 直接对齐按钮左沿;宽度固定,左栏只有 296px,不会溢出视口右侧。 */
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
      <div class="pop-head">更多功能 <span class="x" title="关闭 (Esc)" @click="$emit('close')">✕</span></div>
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
