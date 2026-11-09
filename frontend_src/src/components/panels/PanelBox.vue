<script setup>
const props = defineProps({
  title: { type: String, default: '' },
  tag: { type: String, default: '' },
  state: { type: String, default: 'idle' },
  ready: { type: Boolean, default: false },
})
defineEmits(['refresh'])
</script>

<template>
  <section class="panel">
    <header class="ph">
      <div class="tt">
        <h2>{{ props.title }}</h2>
        <span v-if="props.tag" class="pn-tag">{{ props.tag }}</span>
      </div>
      <button class="rf" :class="{ spin: props.state === 'loading' }" title="重新拉取" @click="$emit('refresh')">
        刷新
      </button>
    </header>
    <div class="pb scroll">
      <div v-if="props.state === 'loading' && !props.ready" class="pn-st">加载中…</div>
      <div v-else-if="props.state === 'error'" class="pn-st err">后端未连接,这个面板暂时拉不到数据</div>
      <slot v-else />
    </div>
  </section>
</template>

<style scoped>
.panel { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.ph {
  display: flex; align-items: center; gap: 10px; flex-shrink: 0;
  padding: 11px 16px 10px; border-bottom: 1px solid var(--line-soft);
}
.tt { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.tt h2 { font-size: 13.5px; font-weight: 650; letter-spacing: -0.01em; color: var(--ink); }
.rf {
  margin-left: auto; flex-shrink: 0;
  padding: 5px 11px; border-radius: var(--r-pill);
  font-size: 11.5px; font-weight: 600; color: var(--ink);
  background: rgba(255, 255, 255, 0.86); border: 1px solid var(--line);
  transition: color 0.14s, background 0.14s, border-color 0.14s;
}
.rf:hover { color: var(--green); background: #fff; border-color: rgba(26, 157, 95, 0.35); }
.rf.spin { color: var(--dim); }
.pb { flex: 1; padding: 14px 16px 18px; }
</style>
