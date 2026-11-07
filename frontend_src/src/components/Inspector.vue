<script setup>
import { computed, onMounted } from 'vue'
import { state, loadProgress, closeDoc } from '../stores/app.js'

onMounted(loadProgress)

const doc = computed(() => state.doc)

/* 大纲:代码取顶层定义,文字取段落首句。P2 换成真解析 */
const outline = computed(() => {
  const d = doc.value
  if (!d) return []
  const src = d.content || ''
  if (d.kind === 'code') {
    return src
      .split('\n')
      .filter((l) => /^(def |class |async def |function |export )/.test(l))
      .map((l) => l.replace(/\s*[({].*$/, '').trim())
      .slice(0, 12)
  }
  return src
    .split(/\n{2,}/)
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 6)
    .map((s) => (s.length > 26 ? s.slice(0, 26) + '…' : s))
})
</script>

<template>
  <aside class="col col--right rail-right">
    <div class="head">
      <div class="seg">
        <button :class="{ on: state.railMode === 'progress' }" @click="state.railMode = 'progress'">进度</button>
        <button :class="{ on: state.railMode === 'context' }" @click="state.railMode = 'context'">
          上下文<span v-if="doc" class="pip" />
        </button>
      </div>
      <button class="collapse" title="收起检视栏" @click="state.railOpen = false">⟩</button>
    </div>

    <div v-if="state.railMode === 'progress'" class="body scroll">
      <section class="sec">
        <div class="sec-h">学习 <em>{{ state.learnStats.total }} 条</em></div>
        <div class="kpi">
          <div><b>{{ state.learnStats.total }}</b><span>条目</span></div>
          <div><b>{{ state.learnStats.avgMastery }}%</b><span>平均掌握</span></div>
          <div><b>{{ state.learnStats.due }}</b><span>今日到期</span></div>
          <div><b>{{ state.learnStats.risk }}</b><span>遗忘风险</span></div>
        </div>
        <div v-if="state.progressState === 'error'" class="empty">后端未连接</div>
        <template v-else>
          <button v-for="(k, i) in state.learning" :key="i" class="row">
            <b>{{ k.title }}</b><span>{{ k.tag }}</span>
          </button>
          <div v-if="!state.learning.length" class="empty">还没有学习记录</div>
        </template>
      </section>
      <section class="sec">
        <div class="sec-h">办公资料 <em>{{ state.office.length }}</em></div>
        <div class="empty">办公资料与进度接口在 P2 接入</div>
      </section>
    </div>

    <div v-else class="body scroll">
      <template v-if="doc">
        <div class="doc-head">
          <div class="kind" :class="doc.kind">{{ doc.kind === 'code' ? 'CLI' : 'TEXT' }}</div>
          <h3>{{ doc.title }}</h3>
          <code class="path">{{ doc.path }}</code>
          <div v-if="doc.meta" class="meta">{{ doc.meta }}</div>
        </div>
        <section class="sec">
          <div class="sec-h">大纲</div>
          <div v-if="!outline.length" class="empty">无可提取的结构</div>
          <div v-for="(o, i) in outline" :key="i" class="out-line"><span class="n">{{ i + 1 }}</span>{{ o }}</div>
        </section>
        <div class="acts"><button @click="closeDoc()">关闭文档</button></div>
      </template>
      <div v-else class="empty">在中间舞台点开一份文件<br />这里会显示它的上下文与大纲</div>
    </div>
  </aside>
</template>

<style scoped>
.rail-right { padding: 0; }
.head {
  display: flex; align-items: center; gap: 8px; flex-shrink: 0;
  padding: 10px 12px 9px; border-bottom: 1px solid var(--line-soft);
}
.seg { display: flex; gap: 2px; padding: 2px; border-radius: var(--r-sm); background: rgba(23, 32, 26, 0.05); }
.seg button {
  display: flex; align-items: center; gap: 4px;
  padding: 4px 11px; border-radius: 7px;
  font-size: 11.5px; color: var(--dim);
  transition: background 0.15s, color 0.15s;
}
.seg button.on { background: #fff; color: var(--ink); font-weight: 600; box-shadow: 0 1px 2px rgba(23, 32, 26, 0.07); }
.seg .pip { width: 5px; height: 5px; border-radius: 50%; background: var(--green); }
.collapse { margin-left: auto; font-size: 13px; color: var(--faint); padding: 2px 5px; }
.collapse:hover { color: var(--ink); }

.body { flex: 1; padding: 12px; }
.sec { margin-bottom: 20px; }
.sec-h {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 10.5px; font-weight: 700; letter-spacing: 0.07em;
  color: var(--faint); text-transform: uppercase; margin-bottom: 8px;
}
.sec-h em { font-family: var(--mono); font-size: 10px; }
.kpi {
  display: grid; grid-template-columns: 1fr 1fr; gap: 6px;
  margin-bottom: 8px;
}
.kpi div {
  padding: 8px 9px; border-radius: var(--r-sm);
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid var(--line-soft);
}
.kpi b { display: block; font-size: 15px; font-weight: 650; letter-spacing: -0.01em; }
.kpi span { font-size: 10px; color: var(--faint); }

.row {
  width: 100%; text-align: left;
  display: flex; flex-direction: column; gap: 2px;
  padding: 7px 8px; border-radius: var(--r-sm); margin-bottom: 2px;
  transition: background 0.14s;
}
.row:hover { background: rgba(255, 255, 255, 0.75); }
.row b { font-size: 12.5px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.row span { font-size: 10.5px; color: var(--faint); }

.doc-head { padding: 0 4px 12px; border-bottom: 1px solid var(--line-soft); margin-bottom: 12px; }
.kind {
  display: inline-block; margin-bottom: 7px;
  padding: 2px 7px; border-radius: var(--r-pill);
  font-family: var(--mono); font-size: 9.5px; letter-spacing: 0.08em;
}
.kind.code { background: var(--cli-bg); color: var(--cli-acc); }
.kind.text { background: var(--surface-2); color: var(--dim); }
.doc-head h3 { font-size: 14px; font-weight: 600; line-height: 1.45; }
.path { display: block; margin-top: 5px; font-family: var(--mono); font-size: 10px; color: var(--faint); word-break: break-all; }
.meta { margin-top: 4px; font-size: 10.5px; color: var(--dim); }

.out-line {
  display: flex; gap: 7px; padding: 5px 6px; border-radius: 7px;
  font-size: 11.5px; color: var(--dim); line-height: 1.5;
}
.out-line:hover { background: rgba(255, 255, 255, 0.7); color: var(--ink); }
.out-line .n { font-family: var(--mono); font-size: 9.5px; color: var(--faint); padding-top: 2px; }

.acts { padding-top: 6px; }
.acts button {
  width: 100%; padding: 7px; border-radius: var(--r-sm);
  font-size: 12px; color: var(--dim);
  background: rgba(255, 255, 255, 0.75); border: 1px solid var(--line-soft);
}
.acts button:hover { color: var(--ink); background: #fff; }
</style>
