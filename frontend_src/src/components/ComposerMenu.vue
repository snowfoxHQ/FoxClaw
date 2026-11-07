<script setup>
defineProps({
  items: { type: Array, default: () => [] },
  index: { type: Number, default: 0 },
  hint: { type: String, default: '' },
})
defineEmits(['pick', 'hover'])
</script>

<template>
  <div class="cm">
    <div class="cm-h">{{ hint }}</div>
    <button
      v-for="(it, i) in items"
      :key="`${it.label}-${i}`"
      class="cm-it"
      :class="{ on: i === index }"
      @mouseenter="$emit('hover', i)"
      @mousedown.prevent="$emit('pick', i)"
    >
      <b>{{ it.label }}</b>
      <span>{{ it.desc }}</span>
      <em v-if="it.tag">{{ it.tag }}</em>
    </button>
    <div v-if="!items.length" class="cm-empty">没有匹配项</div>
    <div class="cm-f">↑↓ 选择 · Enter 确认 · Esc 关闭</div>
  </div>
</template>

<style scoped>
.cm {
  position: absolute; left: 8px; right: 8px; bottom: calc(100% + 6px);
  z-index: 40;
  max-height: 264px; overflow-y: auto;
  padding: 5px;
  border-radius: var(--r-mid);
  background: rgba(255, 255, 255, 0.94);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  backdrop-filter: blur(24px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.9);
  box-shadow: var(--sh-pop);
  animation: cm-in 0.13s ease;
}
@keyframes cm-in { from { opacity: 0; transform: translateY(4px); } }

.cm-h {
  padding: 4px 7px 5px;
  font-size: 11px; font-weight: 700; letter-spacing: 0.07em;
  color: var(--dim); text-transform: uppercase;
}

.cm-it {
  width: 100%;
  display: flex; align-items: baseline; gap: 8px;
  padding: 7px 9px; border-radius: var(--r-sm);
  text-align: left;
}
.cm-it.on { background: var(--surface-2); }
.cm-it b { font-size: 12.5px; font-weight: 700; font-family: var(--mono); flex-shrink: 0; color: var(--ink); }
.cm-it span {
  flex: 1; min-width: 0;
  font-size: 12px; color: var(--ink);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.cm-it em {
  font-style: normal; font-size: 10.5px; font-weight: 600; color: var(--dim);
  padding: 2px 6px; border-radius: var(--r-pill); background: var(--surface-2);
}
.cm-empty { padding: 10px 8px; font-size: 12px; color: var(--dim); }
.cm-f {
  padding: 7px 8px 3px; margin-top: 3px;
  border-top: 1px solid var(--line-soft);
  font-size: 11px; color: var(--dim); font-family: var(--mono);
}
</style>
