<script setup>
import { computed } from 'vue'
import { mdToHTML } from '../lib/md.js'

const props = defineProps({
  doc: { type: Object, required: true },
})

const lines = computed(() =>
  (props.doc.content || '').replace(/\n$/, '').split('\n')
)

const readHTML = computed(() => mdToHTML(props.doc.content))
</script>

<template>
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

  <article v-else class="read scroll">
    <header class="read-head">
      <h2>{{ doc.title }}</h2>
      <div class="read-meta">
        <span v-if="doc.meta">{{ doc.meta }}</span>
        <code v-if="doc.path">{{ doc.path }}</code>
      </div>
    </header>
    <div class="read-body md" v-html="readHTML" />
  </article>
</template>

<style scoped>
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
  font-family: var(--mono); font-size: 11px; font-weight: 700; letter-spacing: 0.06em;
  color: var(--cli-bg); background: var(--cli-acc);
  padding: 2px 8px; border-radius: var(--r-pill);
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
  color: rgba(255, 255, 255, 0.42); user-select: none;
}
.code { color: var(--cli-ink); padding-right: 24px; }

.read { flex: 1; overflow-y: auto; padding: 34px 8px 60px; }
.read-head, .read-body { max-width: 68ch; margin: 0 auto; }
.read-head { padding: 0 20px 18px; border-bottom: 1px solid var(--line-soft); }
.read-head h2 {
  font-family: var(--serif);
  font-size: 23px; font-weight: 600; letter-spacing: -0.01em; line-height: 1.4;
}
.read-meta {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin-top: 8px; font-size: 11.5px; color: var(--dim);
}
.read-meta code { font-family: var(--mono); font-size: 11.5px; color: var(--ink); }
.read-body { padding: 20px 20px 0; }
.read-body :deep(p) {
  font-family: var(--serif);
  font-size: 15px; line-height: 1.92; color: #24302a;
  margin-bottom: 15px;
  text-align: justify;
}
.read-body :deep(h3),
.read-body :deep(h4),
.read-body :deep(h5) {
  font-family: var(--serif);
  color: var(--ink); line-height: 1.5;
  margin: 22px 0 8px;
}
.read-body :deep(h3) { font-size: 18px; font-weight: 650; }
.read-body :deep(h4) { font-size: 15.5px; font-weight: 600; }
.read-body :deep(h5) {
  font-size: 12px; font-weight: 700; letter-spacing: 0.06em;
  color: var(--dim); text-transform: uppercase;
}
.read-body :deep(ul) { margin: 0 0 15px; padding-left: 18px; }
.read-body :deep(li) {
  font-family: var(--serif);
  font-size: 14.5px; line-height: 1.85; color: #24302a;
  margin-bottom: 5px;
}
.read-body :deep(li::marker) { color: var(--faint); }
.read-body :deep(blockquote) {
  margin: 0 0 15px; padding: 8px 14px;
  border-left: 2px solid var(--line);
  font-family: var(--serif); font-size: 14px; line-height: 1.85; color: var(--dim);
}
.read-body :deep(hr) { margin: 20px 0; border: 0; border-top: 1px solid var(--line-soft); }
.read-body :deep(strong) { font-weight: 650; color: #16201b; }
.read-body :deep(code) {
  font-family: var(--mono); font-size: 12px;
  padding: 1px 5px; border-radius: 4px;
  background: rgba(26, 157, 95, 0.09); color: var(--green);
}
.read-body :deep(a) { color: var(--green); border-bottom: 1px solid rgba(26, 157, 95, 0.3); }
.read-body :deep(ol) { margin: 0 0 15px; padding-left: 20px; }
.read-body :deep(h2) {
  font-family: var(--serif);
  font-size: 20px; font-weight: 650; line-height: 1.5;
  color: var(--ink);
  margin: 26px 0 10px;
}

.read-body :deep(pre) {
  margin: 0 0 15px; padding: 11px 13px;
  background: var(--cli-bg); border-radius: var(--r-sm);
  overflow-x: auto;
}
.read-body :deep(pre code) {
  padding: 0; background: none; color: var(--cli-ink);
  font-size: 12px; line-height: 1.72;
}

.read-body :deep(table) { width: 100%; border-collapse: collapse; margin: 0 0 15px; }
.read-body :deep(th),
.read-body :deep(td) {
  padding: 6px 10px; border-bottom: 1px solid var(--line-soft);
  font-size: 13px; text-align: left; vertical-align: top;
}
.read-body :deep(th) { font-size: 11.5px; font-weight: 600; color: var(--dim); }
</style>
