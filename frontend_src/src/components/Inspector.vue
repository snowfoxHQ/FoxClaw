<script setup>
import { computed, onMounted } from 'vue'
import QuickIngest from './QuickIngest.vue'
import { state, loadProgress, closeDoc, setModule } from '../stores/app.js'
import { panels, loadPanel, openKu, startReview } from '../stores/panels.js'
import { fmtTime } from '../lib/fmt.js'

/* 右栏三块数据与对应面板共用同一份,进来就保证已加载 */
onMounted(() => {
  loadProgress()
  loadPanel('knowledge')
  loadPanel('review')
})

const doc = computed(() => state.doc)
const kb = panels.knowledge
const rv = panels.review

/* 今日待办 = 到期 + 新卡(SM-2 队列就是这个口径) */
const reviewTodo = computed(() => (rv.stats.due || 0) + (rv.stats.fresh || 0))
const masteryPct = computed(() => Math.round((rv.stats.avg_mastery || 0) * 100))

function goReview() {
  state.railMode = 'progress'
  setModule('review')
  startReview()
}

/* 右栏只留 10 条,多的走「更多」到知识库面板看全量 */
const LIMIT = 10
const recentKb = computed(() => kb.recent.slice(0, LIMIT))
const kbMore = computed(() => kb.recentTotal > recentKb.value.length)

function moreKb() {
  state.railMode = 'progress'
  setModule('knowledge')
}

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
      <QuickIngest />

      <section class="sec">
        <div class="sec-h">学习 <em>SM-2</em></div>
        <div class="kpi">
          <div><b>{{ rv.stats.due }}</b><span>今日到期</span></div>
          <div><b>{{ rv.stats.fresh }}</b><span>新卡</span></div>
          <div><b>{{ masteryPct }}%</b><span>平均掌握</span></div>
          <div><b>{{ rv.stats.risk.length }}</b><span>遗忘风险</span></div>
        </div>
        <div v-if="rv.state === 'error'" class="empty">后端未连接</div>
        <template v-else>
          <button class="go" :disabled="!reviewTodo" @click="goReview">
            {{ reviewTodo ? `开始复习 ${reviewTodo} 张` : '今天没有要复习的' }}
          </button>
          <div v-if="!rv.stats.cards" class="empty">录入的知识会自动变成复习卡</div>
        </template>
      </section>
      <section class="sec">
        <div class="sec-h">办公 <em>OfficeAgent</em></div>
        <div class="kpi">
          <div><b>{{ state.officeStats.waypoints }}</b><span>行程点</span></div>
          <div><b>{{ state.officeStats.conflicts }}</b><span>行程冲突</span></div>
          <div><b>{{ state.officeStats.alerts }}</b><span>告警</span></div>
          <div><b>{{ state.officeStats.active }}</b><span>进行中</span></div>
        </div>
        <div class="sub-h">最近知识 <em>{{ kb.recentTotal }}</em></div>
        <div v-if="kb.state === 'error'" class="empty">后端未连接</div>
        <template v-else>
          <button v-for="k in recentKb" :key="k.ku_id" class="row" :title="k.summary || k.name" @click="openKu(k.ku_id)">
            <b>{{ k.name }}</b><span>{{ k.ku_type }} · {{ fmtTime(k.created_at) }}</span>
          </button>
          <div v-if="!recentKb.length" class="empty">
            还没有录入知识。到「学习库」写一句就能收录,长文档走知识星球。
          </div>
          <button v-if="kbMore" class="more" title="到学习库看全部" @click="moreKb">
            <span class="bars"><i /><i /><i /></span>更多 {{ kb.recentTotal }} 条
          </button>
        </template>
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
.seg { display: flex; gap: 2px; padding: 2px; border-radius: var(--r-sm); background: rgba(64, 50, 32, 0.055); }
.seg button {
  display: flex; align-items: center; gap: 4px;
  padding: 4px 11px; border-radius: 7px;
  font-size: 11.5px; color: var(--dim);
  transition: background 0.15s, color 0.15s;
}
.seg button.on { background: #fff; color: var(--ink); font-weight: 600; box-shadow: 0 1px 2px rgba(64, 50, 32, 0.09); }
.seg .pip { width: 5px; height: 5px; border-radius: 50%; background: var(--green); }
.collapse { margin-left: auto; font-size: 13px; color: var(--faint); padding: 2px 5px; }
.collapse:hover { color: var(--ink); }

.body { flex: 1; padding: 12px; }
.sec { margin-bottom: 20px; }
.sec-h {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 11.5px; font-weight: 700; letter-spacing: 0.07em;
  color: var(--dim); text-transform: uppercase; margin-bottom: 8px;
}
.sec-h em { font-family: var(--mono); font-size: 11.5px; font-weight: 700; color: var(--ink); }
.kpi {
  display: grid; grid-template-columns: 1fr 1fr; gap: 6px;
  margin-bottom: 8px;
}
.kpi div {
  padding: 8px 9px; border-radius: var(--r-sm);
  /* 纸面上要的是「白卡」而不是「更浅的纸」:0.72 的白压在暖底上几乎看不出边界 */
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid var(--line);
}
.kpi b { display: block; font-size: 16px; font-weight: 700; letter-spacing: -0.01em; color: var(--w-ink); }
.kpi span { font-size: 11.5px; font-weight: 500; color: var(--w-dim); }
.go {
  width: 100%; padding: 8px; border-radius: var(--r-sm);
  font-size: 12px; font-weight: 600; color: #fff;
  background: var(--green);
  transition: opacity 0.15s;
}
.go:disabled { color: var(--w-dim); background: rgba(64, 50, 32, 0.07); font-weight: 600; border: 1px solid var(--line-soft); }
.go:not(:disabled):hover { opacity: 0.9; }

.sub-h {
  display: flex; justify-content: space-between;
  padding: 5px 2px 6px;
  font-size: 11px; font-weight: 600; color: var(--w-dim);
}
.sub-h em { font-style: normal; font-family: var(--mono); }

.row {
  width: 100%; text-align: left;
  display: flex; flex-direction: column; gap: 2px;
  padding: 8px 9px; border-radius: var(--r-sm); margin-bottom: 2px;
  transition: background 0.14s;
}
.row:hover { background: rgba(255, 255, 255, 0.75); }
.row b { font-size: 13px; font-weight: 600; color: var(--w-ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.row span { font-size: 11.5px; color: var(--w-dim); }

/* 「更多」:三条杠 + 文字,点进知识库看全量 */
.more {
  width: 100%; display: flex; align-items: center; justify-content: center; gap: 6px;
  margin-top: 4px; padding: 7px 9px;
  border-radius: var(--r-sm);
  font-size: 12px; font-weight: 600; color: var(--w-dim);
  border: 1px dashed var(--line);
  transition: color 0.15s, border-color 0.15s, background 0.15s;
}
.more:hover {
  color: var(--green); border-color: rgba(26, 157, 95, 0.32);
  background: rgba(26, 157, 95, 0.05);
}
.bars { display: flex; flex-direction: column; gap: 2px; }
.bars i { display: block; width: 10px; height: 1.5px; border-radius: 1px; background: currentColor; opacity: 0.75; }

.doc-head { padding: 0 4px 12px; border-bottom: 1px solid var(--line-soft); margin-bottom: 12px; }
.kind {
  display: inline-block; margin-bottom: 7px;
  padding: 3px 8px; border-radius: var(--r-pill);
  font-family: var(--mono); font-size: 11px; font-weight: 700; letter-spacing: 0.08em;
}
.kind.code { background: var(--cli-bg); color: var(--cli-acc); }
.kind.text { background: var(--surface-2); color: var(--w-ink); }
.doc-head h3 { font-size: 15px; font-weight: 650; line-height: 1.45; color: var(--w-ink); }
.path { display: block; margin-top: 6px; font-family: var(--mono); font-size: 11px; color: var(--w-dim); word-break: break-all; }
.meta { margin-top: 5px; font-size: 11.5px; color: var(--w-dim); }

.out-line {
  display: flex; gap: 7px; padding: 6px 7px; border-radius: 7px;
  font-size: 12px; color: var(--w-ink); line-height: 1.55;
}
.out-line:hover { background: rgba(255, 255, 255, 0.7); color: var(--w-ink); }
.out-line .n { font-family: var(--mono); font-size: 10.5px; color: var(--w-dim); padding-top: 2px; }

.acts { padding-top: 6px; }
.acts button {
  width: 100%; padding: 8px; border-radius: var(--r-sm);
  font-size: 12.5px; font-weight: 600; color: var(--w-ink);
  background: rgba(255, 255, 255, 0.8); border: 1px solid var(--line);
}
.acts button:hover { color: var(--ink); background: #fff; }
</style>
