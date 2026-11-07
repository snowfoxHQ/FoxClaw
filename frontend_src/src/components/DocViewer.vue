<script setup>
import { computed } from 'vue'

const props = defineProps({
  doc: { type: Object, required: true },
})

const lines = computed(() =>
  (props.doc.content || '').replace(/\n$/, '').split('\n')
)
const paragraphs = computed(() =>
  (props.doc.content || '').split(/\n{2,}/).map((s) => s.trim()).filter(Boolean)
)
</script>

<template>
  <!-- 代码 → CLI 皮肤:暗色、等宽、行号、横向滚动。整栏不变暗,只有正文块变暗。 -->
  <div v-if="doc.kind === 'code'" class="cli">
    <div class="term-bar">
      <span class="tl" /><span class="tl" /><span class="tl" />
      <code class="path">{{ doc.path }}</code>
      <span class="lang">{{ doc.lang }}</span>
    </div>
    <div class="term-body scroll">
      <div v-for="(l, i) in lines" :key="i" class="row">
        <span class="no">{{ i + 1 }}</span>
        <span class="code">{{ l || ' ' }}</span>
      </div>
    </div>
  </div>

  <!-- 文字 → 阅读版:衬线正文、68ch 行宽、留白优先 -->
  <article v-else class="read scroll">
    <header class="read-head">
      <h2>{{ doc.title }}</h2>
      <div class="read-meta">
        <span v-if="doc.meta">{{ doc.meta }}</span>
        <code v-if="doc.path">{{ doc.path }}</code>
      </div>
    </header>
    <div class="read-body">
      <p v-for="(p, i) in paragraphs" :key="i">{{ p }}</p>
    </div>
  </article>
</template>

<style scoped>
/* ═══ CLI 皮肤 ═══ */
.cli {
  flex: 1; min-width: 0; display: flex; flex-direction: column;
  margin: 8px; border-radius: var(--r-mid); overflow: hidden;
  background: var(--cli-bg);
  border: 1px solid rgba(255, 255, 255, 0.06);
  box-shadow: 0 18px 44px rgba(11, 15, 10, 0.34);
}
.term-bar {
  display: flex; align-items: center; gap: 6px; flex-shrink: 0;
  padding: 9px 12px;
  background: var(--cli-bg-2);
  border-bottom: 1px solid var(--cli-line);
}
.tl { width: 9px; height: 9px; border-radius: 50%; background: rgba(255, 255, 255, 0.14); }
.tl:first-child { background: rgba(255, 104, 92, 0.62); }
.tl:nth-child(2) { background: rgba(226, 192, 122, 0.62); }
.tl:nth-child(3) { background: rgba(127, 224, 140, 0.58); }
.term-bar .path {
  margin-left: 8px; font-family: var(--mono); font-size: 11.5px;
  color: var(--cli-dim);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.term-bar .lang {
  margin-left: auto; flex-shrink: 0;
  font-family: var(--mono); font-size: 10px; letter-spacing: 0.06em;
  color: var(--cli-bg); background: var(--cli-acc);
  padding: 1px 7px; border-radius: var(--r-pill);
}
.term-body {
  flex: 1; overflow: auto; padding: 10px 0 16px;
  font-family: var(--mono); font-size: 12.5px; line-height: 1.72;
}
.term-body::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.16); }
.row { display: flex; white-space: pre; }
.row:hover { background: rgba(255, 255, 255, 0.035); }
.no {
  flex-shrink: 0; width: 48px; text-align: right; padding-right: 14px;
  color: rgba(255, 255, 255, 0.2); user-select: none;
}
.code { color: var(--cli-ink); padding-right: 24px; }

/* ═══ 阅读皮肤 ═══ */
.read { flex: 1; overflow-y: auto; padding: 34px 8px 60px; }
.read-head, .read-body { max-width: 68ch; margin: 0 auto; }
.read-head { padding: 0 20px 18px; border-bottom: 1px solid var(--line-soft); }
.read-head h2 {
  font-family: var(--serif);
  font-size: 23px; font-weight: 600; letter-spacing: -0.01em; line-height: 1.4;
}
.read-meta {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin-top: 8px; font-size: 11px; color: var(--faint);
}
.read-meta code { font-family: var(--mono); font-size: 10.5px; }
.read-body { padding: 20px 20px 0; }
.read-body p {
  font-family: var(--serif);
  font-size: 15px; line-height: 1.92; color: #24302a;
  margin-bottom: 15px;
  text-align: justify;
}
</style>
