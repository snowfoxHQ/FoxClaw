<script setup>
import { nextTick, ref, watch } from 'vue'
import { chat } from '../stores/chat.js'

const box = ref(null)
let stick = true

function onScroll() {
  const el = box.value
  if (!el) return
  stick = el.scrollHeight - el.scrollTop - el.clientHeight < 80
}

/* 依赖「长度和」而不是整个数组:流式时只有最后一个对象在变,深度 watch 会打爆 */
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
</script>

<template>
  <div ref="box" class="ms scroll" @scroll="onScroll">
    <div class="ms-in">
      <article v-for="(m, i) in chat.messages" :key="i" class="msg" :class="m.role">
        <!-- 用户 -->
        <div v-if="m.role === 'user'" class="u-body">
          <div v-if="m.attachments && m.attachments.length" class="u-atts">
            <span v-for="(a, j) in m.attachments" :key="j" class="u-att">{{ a.name }}</span>
          </div>
          <p class="u-text">{{ m.content }}</p>
          <div class="u-time">{{ fmtTime(m.ts) }}</div>
        </div>

        <!-- 助手 -->
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

          <p v-if="m.content" class="a-text">{{ m.content }}<span v-if="m.streaming" class="caret" /></p>
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

/* ── 用户 ── */
.msg.user { display: flex; justify-content: flex-end; }
.u-body { max-width: 78%; display: flex; flex-direction: column; align-items: flex-end; gap: 5px; }
.u-atts { display: flex; gap: 5px; flex-wrap: wrap; justify-content: flex-end; }
.u-att {
  font-size: 10.5px; padding: 2px 8px; border-radius: var(--r-pill);
  color: var(--dim); background: rgba(255, 255, 255, 0.9); border: 1px solid var(--line-soft);
}
.u-text {
  padding: 9px 14px; border-radius: 14px 14px 4px 14px;
  font-size: 13.5px; line-height: 1.65; color: var(--ink);
  background: linear-gradient(180deg, #ffffff, #f2f8ee);
  border: 1px solid var(--line-soft);
  box-shadow: 0 1px 2px rgba(23, 32, 26, 0.05);
  white-space: pre-wrap; word-break: break-word;
}
.u-time { font-size: 9.5px; color: var(--faint); font-family: var(--mono); }

/* ── 助手 ── */
.a-body { display: flex; flex-direction: column; gap: 9px; }
.a-head { display: flex; align-items: center; gap: 7px; font-size: 10.5px; color: var(--faint); }
.avatar {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--green); box-shadow: 0 0 6px rgba(26, 157, 95, 0.5);
}
.avatar.live { animation: pulse 1.1s ease-in-out infinite; }
@keyframes pulse { 50% { opacity: 0.35; transform: scale(0.8); } }
.who { font-weight: 600; color: var(--dim); }
.a-head .stage {
  padding: 1px 7px; border-radius: var(--r-pill);
  background: rgba(26, 157, 95, 0.1); color: var(--green);
  font-family: var(--mono); font-size: 9.5px;
}
.a-head .live-tag { color: var(--green); font-size: 9.5px; }
.a-time { margin-left: auto; font-family: var(--mono); }

.a-text {
  font-size: 13.5px; line-height: 1.8; color: var(--ink);
  white-space: pre-wrap; word-break: break-word;
}
.a-text.waiting { color: var(--faint); }
.caret {
  display: inline-block; width: 6px; height: 14px; margin-left: 2px;
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
  padding: 6px 10px; cursor: pointer;
  font-size: 11px; color: var(--dim);
  list-style: none;
}
.fold summary::-webkit-details-marker { display: none; }
.fold summary::before { content: '▸'; font-size: 8px; color: var(--faint); }
.fold[open] summary::before { content: '▾'; }
.fold summary em { font-style: normal; font-family: var(--mono); font-size: 9.5px; color: var(--faint); }
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
.ev-row b { font-size: 11px; font-weight: 600; color: var(--ink); }
.ev-st { font-family: var(--mono); font-size: 9px; color: var(--faint); }
.ev-dot { width: 5px; height: 5px; border-radius: 50%; background: var(--amber); flex-shrink: 0; }
.ev-dot.verified { background: var(--green); }
.ev-dot.failed { background: var(--red); }
.ev-link, .ev-file { font-size: 10.5px; color: var(--accent); text-decoration: none; }
.ev-file { color: var(--dim); }
.ev-link:hover { text-decoration: underline; }

.notice {
  padding: 6px 10px; border-radius: var(--r-sm);
  font-size: 11.5px; color: var(--amber);
  background: rgba(200, 140, 40, 0.09);
}
.err {
  padding: 7px 10px; border-radius: var(--r-sm);
  font-size: 11.5px; color: var(--red);
  background: rgba(229, 72, 77, 0.07);
  border: 1px solid rgba(229, 72, 77, 0.16);
  white-space: pre-wrap;
}
</style>
