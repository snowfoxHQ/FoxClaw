<script setup>
import { computed, ref, onUnmounted } from 'vue'
import { panels, ingestKnowledge } from '../stores/panels.js'

const kp = panels.knowledge
const draft = ref('')
const title = ref('')
let timer = null

const busy = computed(() => kp.ingest === 'busy')
const msg = computed(() =>
  busy.value ? '抽取中,可能要几十秒…' : (kp.ingestErr || kp.ingestMsg)
)
const kind = computed(() => (busy.value ? 'busy' : kp.ingestErr ? 'err' : 'ok'))

function clearLater() {
  clearTimeout(timer)
  timer = setTimeout(() => { kp.ingest = 'idle'; kp.ingestMsg = ''; kp.ingestErr = '' }, 8000)
}

async function go() {
  const text = draft.value.trim()
  if (busy.value || !text) return
  draft.value = ''
  try {
    await ingestKnowledge({ text, title: title.value })
    title.value = ''
  } catch {
  }
  clearLater()
}

onUnmounted(() => clearTimeout(timer))
</script>

<template>
  <section class="qi">
    <div class="qi-h">
      收录知识 <em>短知识 · 长文档走知识星球</em>
    </div>
    <textarea
      v-model="draft"
      class="qi-ta"
      rows="2"
      placeholder="写一句就算一条,Ctrl+Enter 收录"
      @keydown.ctrl.enter.prevent="go"
    />
    <div class="qi-b">
      <input v-model="title" class="qi-title" placeholder="标题(可选)" />
      <button class="qi-go" :disabled="busy || !draft.trim()" @click="go">
        {{ busy ? '抽取中…' : '收录' }}
      </button>
    </div>
    <div v-if="msg" class="qi-msg" :class="kind">{{ msg }}</div>
  </section>
</template>

<style scoped>
.qi {
  padding: 0 0 10px;
  margin-bottom: 14px;
  border-bottom: 1px solid var(--line-soft);
}
.qi-h {
  display: flex; align-items: baseline; gap: 6px;
  margin-bottom: 7px;
  font-size: 11px; font-weight: 700; letter-spacing: 0.07em;
  text-transform: uppercase; color: var(--dim);
}
.qi-h em {
  font-style: normal; font-weight: 500; letter-spacing: 0;
  text-transform: none; font-size: 11px; color: var(--dim);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.qi-ta {
  display: block; width: 100%; box-sizing: border-box;
  resize: vertical; min-height: 46px; max-height: 140px;
  padding: 7px 9px; border-radius: var(--r-sm);
  font-family: var(--sans); font-size: 12.5px; line-height: 1.6;
  color: var(--ink); background: #fff;
  border: 1px solid var(--line);
  transition: border-color 0.15s;
}
.qi-ta::placeholder { color: var(--dim); }
.qi-ta:focus { outline: none; border-color: rgba(26, 157, 95, 0.42); }
.qi-b { display: flex; gap: 6px; margin-top: 6px; }
.qi-title {
  flex: 1 1 auto; min-width: 0;
  padding: 5px 8px; border-radius: var(--r-pill);
  font-size: 11.5px; color: var(--ink);
  background: rgba(255, 255, 255, 0.86);
  border: 1px solid var(--line);
}
.qi-title::placeholder { color: var(--dim); }
.qi-title:focus { outline: none; border-color: rgba(26, 157, 95, 0.35); }
.qi-go {
  flex-shrink: 0;
  padding: 6px 13px; border-radius: var(--r-pill);
  font-size: 11.5px; font-weight: 600; color: #fff;
  background: var(--green);
  border: 1px solid var(--green);
  transition: opacity 0.15s;
}
.qi-go:disabled {
  background: rgba(23, 32, 26, 0.07); color: var(--dim);
  border-color: var(--line);
}
.qi-msg { margin-top: 6px; font-size: 11px; line-height: 1.5; }
.qi-msg.ok { color: var(--green); }
.qi-msg.busy { color: var(--dim); }
.qi-msg.err { color: #b4453c; }
</style>
