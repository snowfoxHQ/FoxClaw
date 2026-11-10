<script setup>
import { nextTick, ref, watch } from 'vue'
import { chat, retry } from '../stores/chat.js'
import { mdToHTML } from '../lib/md.js'

const box = ref(null)
let stick = true

function onScroll() {
  const el = box.value
  if (!el) return
  stick = el.scrollHeight - el.scrollTop - el.clientHeight < 80
}

watch(
  () =>
    chat.messages
      .map((m) => `${(m.content || '').length}:${(m.thinking || '').length}:${m.streaming ? 1 : 0}`)
      .join('|'),
  async () => {
    if (!stick) return
    await nextTick()
    const el = box.value
    if (el) el.scrollTop = el.scrollHeight
  }
)

function fmtTime(ts) {
  if (!ts) return ''
  return new Date(ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
}

function isUrl(x) {
  return /^https?:\/\//i.test((x && x.ref) || '')
}

function stepText(s) {
  if (typeof s === 'string') return s
  return s.desc || s.task || s.label || JSON.stringify(s)
}

const SRC_CN = { memory: '记忆', knowledge: '知识', web: '联网', agent: '态势' }
function srcLabel(s) {
  return SRC_CN[s] || s || '证据'
}

const copiedKey = ref(-1)
let copyTimer = 0

async function copy(m, i) {
  const text = m.content || ''
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try {
      document.execCommand('copy')
    } catch {
    }
    ta.remove()
  }
  copiedKey.value = i
  clearTimeout(copyTimer)
  copyTimer = setTimeout(() => (copiedKey.value = -1), 1600)
}

function canRetry(i) {
  return i === chat.messages.length - 1
}
</script>

<template>
  <div ref="box" class="ms scroll" @scroll="onScroll">
    <div class="ms-in">
      <article v-for="(m, i) in chat.messages" :key="i" class="msg" :class="m.role">
        <div v-if="m.role === 'user'" class="u-body">
          <div v-if="m.attachments && m.attachments.length" class="u-atts">
            <span v-for="(a, j) in m.attachments" :key="j" class="u-att">{{ a.name }}</span>
          </div>
          <p class="u-text">{{ m.content }}</p>
          <div class="u-time">{{ fmtTime(m.ts) }}</div>
        </div>

        <div v-else class="a-body">
          <div class="a-head">
            <span class="avatar" :class="{ live: m.streaming }" />
            <span class="who">主管 Agent</span>
            <span v-if="m.stage" class="stage">◌ {{ m.stage }}</span>
            <span v-if="m.streaming" class="live-tag">生成中</span>
            <span class="a-time">{{ fmtTime(m.ts) }}</span>
          </div>

          <details v-if="m.thinking" class="fold think">
            <summary>推理过程 <em>{{ m.thinking.length }} 字</em></summary>
            <pre class="fold-body">{{ m.thinking }}</pre>
          </details>

          <details v-if="m.plan && (m.plan.steps || []).length" class="fold plan" open>
            <summary>
              执行计划 <em>{{ m.plan.steps.length }} 步 · {{ m.plan.flow || 'single' }}</em>
            </summary>
            <ol class="fold-body">
              <li v-for="(s, k) in m.plan.steps" :key="k">{{ stepText(s) }}</li>
            </ol>
          </details>

          <div v-if="m.search" class="sres">
            <div class="sres-h">
              <span class="sroute">{{ m.search.route }}</span>
              <span>置信度 {{ Math.round((m.search.confidence || 0) * 100) }}%</span>
              <span>{{ m.search.iterations }} 轮迭代</span>
            </div>
            <div v-for="(n, k) in m.search.nodes" :key="k" class="srow">
              <span class="ssrc" :class="n.source">{{ srcLabel(n.source) }}</span>
              <p>{{ n.fact }}</p>
              <em>{{ Math.round((n.confidence || 0) * 100) }}</em>
            </div>
          </div>

          <div
            v-if="m.content"
            class="a-text"
            :class="{ streaming: m.streaming }"
            v-html="mdToHTML(m.content)"
          />
          <p v-else-if="m.streaming" class="a-text waiting"><span class="dots">···</span></p>

          <div v-if="m.notice" class="notice">{{ m.notice }}</div>
          <div v-if="m.error" class="err">✕ {{ m.error }}</div>

          <details v-if="m.evidence && m.evidence.length" class="fold ev">
            <summary>执行证据 <em>{{ m.evidence.length }} 项</em></summary>
            <div class="fold-body">
              <div v-for="(e, k) in m.evidence" :key="k" class="ev-row">
                <span class="ev-dot" :class="e.status || 'unverified'" />
                <b>{{ e.tool || e.kind || '工具' }}</b>
                <span class="ev-st">{{ e.status || e.reason || '' }}</span>
                <template v-for="(x, y) in e.evidence || []" :key="y">
                  <a v-if="isUrl(x)" class="ev-link" :href="x.ref" target="_blank" rel="noreferrer">
                    {{ x.label || x.ref }}
                  </a>
                  <span v-else class="ev-file">{{ x.label || x.ref }}</span>
                </template>
              </div>
            </div>
          </details>

          <div v-if="!m.streaming && (m.content || m.error)" class="a-acts">
            <button v-if="m.content" class="mini" @click="copy(m, i)">
              {{ copiedKey === i ? '已复制' : '复制' }}
            </button>
            <button v-if="canRetry(i)" class="mini" @click="retry(m)">重试</button>
          </div>
        </div>
      </article>
    </div>
  </div>
</template>

<style scoped>
.ms { flex: 1; min-height: 0; overflow-y: auto; }
.ms-in {
  max-width: 780px; margin: 0 auto;
  padding: 18px 22px 26px;
  display: flex; flex-direction: column; gap: 20px;
}

.msg.user { display: flex; justify-content: flex-end; }
.u-body { max-width: 78%; display: flex; flex-direction: column; align-items: flex-end; gap: 5px; }
.u-atts { display: flex; gap: 5px; flex-wrap: wrap; justify-content: flex-end; }
.u-att {
  font-size: 11.5px; padding: 3px 9px; border-radius: var(--r-pill);
  color: var(--ink); background: rgba(255, 255, 255, 0.9); border: 1px solid var(--line);
}
.u-text {
  padding: 9px 14px; border-radius: 14px 14px 4px 14px;
  font-size: 13.5px; line-height: 1.65; color: var(--ink);
  background: linear-gradient(180deg, #ffffff, #f2f8ee);
  border: 1px solid var(--line-soft);
  box-shadow: 0 1px 2px rgba(23, 32, 26, 0.05);
  white-space: pre-wrap; word-break: break-word;
}
.u-time { font-size: 11px; color: var(--faint); font-family: var(--mono); }

.a-body { display: flex; flex-direction: column; gap: 9px; }
.a-head { display: flex; align-items: center; gap: 7px; font-size: 11.5px; color: var(--dim); }
.avatar {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--green); box-shadow: 0 0 6px rgba(26, 157, 95, 0.5);
}
.avatar.live { animation: pulse 1.1s ease-in-out infinite; }
@keyframes pulse { 50% { opacity: 0.35; transform: scale(0.8); } }
.who { font-weight: 700; color: var(--ink); }
.a-head .stage {
  padding: 2px 8px; border-radius: var(--r-pill);
  background: rgba(26, 157, 95, 0.12); color: var(--green);
  font-family: var(--mono); font-size: 10.5px; font-weight: 600;
}
.a-head .live-tag { color: var(--green); font-size: 10.5px; font-weight: 600; }
.a-time { margin-left: auto; font-family: var(--mono); }

.a-text {
  font-size: 13.5px; line-height: 1.8; color: var(--ink);
  word-break: break-word;
  white-space: pre-wrap;
}
.a-text :deep(pre),
.a-text :deep(pre code) { white-space: pre; }
.a-text.waiting { color: var(--faint); }

.a-text :deep(p) { margin: 0 0 8px; }
.a-text :deep(p:last-child) { margin-bottom: 0; }
.a-text :deep(h2),
.a-text :deep(h3),
.a-text :deep(h4),
.a-text :deep(h5) {
  font-family: var(--serif); color: var(--ink);
  line-height: 1.5; margin: 14px 0 6px;
}
.a-text :deep(h2) { font-size: 16.5px; font-weight: 650; }
.a-text :deep(h3) { font-size: 15px; font-weight: 650; }
.a-text :deep(h4),
.a-text :deep(h5) { font-size: 13.5px; font-weight: 650; }
.a-text :deep(ul),
.a-text :deep(ol) { margin: 0 0 8px; padding-left: 20px; }
.a-text :deep(li) { margin-bottom: 3px; }
.a-text :deep(li::marker) { color: var(--faint); }
.a-text :deep(blockquote) {
  margin: 0 0 8px; padding: 3px 10px;
  border-left: 2px solid var(--line);
  color: var(--dim);
}
.a-text :deep(hr) { margin: 12px 0; border: 0; border-top: 1px solid var(--line-soft); }
.a-text :deep(strong) { font-weight: 650; color: var(--ink); }
.a-text :deep(del) { color: var(--faint); }
.a-text :deep(code) {
  font-family: var(--mono); font-size: 12px;
  padding: 1px 5px; border-radius: 4px;
  background: rgba(26, 157, 95, 0.09); color: var(--green);
  white-space: pre-wrap;
}
.a-text :deep(pre) {
  margin: 8px 0; padding: 10px 12px;
  background: #17211c; border-radius: var(--r-sm);
  overflow-x: auto;
}
.a-text :deep(pre code) {
  padding: 0; background: none;
  color: #dfe9e2; font-size: 12px; line-height: 1.7;
  white-space: pre;
}
.a-text :deep(table) {
  display: block; overflow-x: auto;
  border-collapse: collapse; margin: 8px 0;
  font-size: 12.5px;
}
.a-text :deep(th),
.a-text :deep(td) {
  padding: 4px 9px; border-bottom: 1px solid var(--line-soft);
  text-align: left; vertical-align: top;
}
.a-text :deep(th) { color: var(--dim); font-size: 11.5px; }
.a-text :deep(a) { color: var(--green); border-bottom: 1px solid rgba(26, 157, 95, 0.3); }

.a-text.streaming > :last-child::after {
  content: '';
  display: inline-block; width: 6px; height: 13px; margin-left: 3px;
  vertical-align: -2px; background: var(--green);
  animation: blink 1s step-end infinite;
}

@keyframes blink { 50% { opacity: 0; } }
.dots { animation: blink 1.2s step-end infinite; letter-spacing: 2px; }

.fold {
  border-radius: var(--r-sm);
  background: rgba(241, 244, 239, 0.7);
  border: 1px solid var(--line-soft);
  overflow: hidden;
}
.fold summary {
  display: flex; align-items: center; gap: 7px;
  padding: 7px 11px; cursor: pointer;
  font-size: 12px; font-weight: 600; color: var(--ink);
  list-style: none;
}
.fold summary::-webkit-details-marker { display: none; }
.fold summary::before { content: '▸'; font-size: 10px; color: var(--dim); }
.fold[open] summary::before { content: '▾'; }
.fold summary em { font-style: normal; font-family: var(--mono); font-size: 10.5px; color: var(--dim); }
.fold-body {
  margin: 0; padding: 2px 12px 10px;
  font-size: 11.5px; line-height: 1.7; color: var(--dim);
  white-space: pre-wrap; word-break: break-word;
  max-height: 280px; overflow-y: auto;
}
.fold-body ol { margin: 0; padding-left: 18px; }
.fold.think .fold-body { font-family: var(--mono); font-size: 11px; }
.fold.ev .fold-body { padding-bottom: 8px; }

.ev-row { display: flex; align-items: baseline; gap: 7px; padding: 3px 0; flex-wrap: wrap; }
.ev-row b { font-size: 12px; font-weight: 700; color: var(--ink); }
.ev-st { font-family: var(--mono); font-size: 10.5px; color: var(--dim); }
.ev-dot { width: 5px; height: 5px; border-radius: 50%; background: var(--amber); flex-shrink: 0; }
.ev-dot.verified { background: var(--green); }
.ev-dot.failed { background: var(--red); }
.ev-link, .ev-file { font-size: 11.5px; color: var(--accent); text-decoration: none; }
.ev-file { color: var(--dim); }
.ev-link:hover { text-decoration: underline; }

.sres {
  border-radius: var(--r-sm);
  background: rgba(255, 255, 255, 0.75);
  border: 1px solid var(--line-soft);
  overflow: hidden;
}
.sres-h {
  display: flex; align-items: center; gap: 10px;
  padding: 7px 11px;
  border-bottom: 1px solid var(--line-soft);
  font-size: 10.5px; color: var(--dim); font-family: var(--mono);
}
.sres-h .sroute {
  padding: 2px 7px; border-radius: var(--r-pill);
  color: var(--green); background: rgba(26, 157, 95, 0.12);
  font-weight: 700;
}
.srow {
  display: flex; align-items: baseline; gap: 8px;
  padding: 6px 10px;
  border-bottom: 1px solid rgba(23, 32, 26, 0.04);
}
.srow:last-child { border-bottom: 0; }
.ssrc {
  flex-shrink: 0; font-size: 10.5px; font-weight: 600; padding: 2px 6px; border-radius: 4px;
  color: var(--dim); background: var(--surface-2);
}
.ssrc.knowledge { color: var(--green); background: rgba(26, 157, 95, 0.12); }
.ssrc.web { color: var(--accent); background: rgba(10, 132, 255, 0.12); }
.srow p {
  flex: 1; min-width: 0;
  font-size: 12px; line-height: 1.6; color: var(--ink);
  word-break: break-word;
}
.srow em { font-style: normal; font-family: var(--mono); font-size: 10.5px; color: var(--dim); }

.notice {
  padding: 6px 10px; border-radius: var(--r-sm);
  font-size: 11.5px; color: var(--amber);
  background: rgba(200, 140, 40, 0.09);
}

.a-acts { display: flex; gap: 6px; opacity: 0; transition: opacity 0.13s; }
.msg:hover .a-acts,
.a-acts:focus-within { opacity: 1; }
.mini {
  font-size: 11.5px; padding: 3px 10px;
  border-radius: var(--r-pill);
  color: var(--dim); background: rgba(255, 255, 255, 0.86);
  border: 1px solid var(--line);
  transition: color 0.13s, border-color 0.13s, background 0.13s;
}
.mini:hover {
  color: var(--green); background: #fff;
  border-color: rgba(26, 157, 95, 0.35);
}

@media (hover: none) {
  .a-acts { opacity: 1; }
}
.err {
  padding: 7px 10px; border-radius: var(--r-sm);
  font-size: 11.5px; color: var(--red);
  background: rgba(229, 72, 77, 0.07);
  border: 1px solid rgba(229, 72, 77, 0.16);
  white-space: pre-wrap;
}
</style>
