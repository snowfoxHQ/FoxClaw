<script setup>
/* 复习面板 = SM-2 账本 + 今日队列 + 遗忘曲线。
   卡片就是知识库里的 KU(复习状态在 KU.extra.review 里),不另存一份。 */
import { computed, onMounted, ref } from 'vue'
import PanelBox from './PanelBox.vue'
import {
  panels, loadPanel, refreshPanel, startReview, revealCard, gradeCard, openKu,
} from '../../stores/panels.js'

const rv = panels.review
const err = ref('')

onMounted(() => loadPanel('review'))

const st = computed(() => rv.stats || {})
const card = computed(() => rv.queue[rv.index] || null)
const total = computed(() => rv.queue.length)
const pos = computed(() => Math.min(rv.index + 1, total.value))

const masteryPct = computed(() => Math.round((st.value.avg_mastery || 0) * 100))
const todo = computed(() => (st.value.due || 0) + (st.value.fresh || 0))

/* 遗忘曲线:Ebbinghaus R = e^(-t/S)。
   SM-2 的语义是「复习时保留率约 90%」,由 R=0.9 反推稳定度
   S ≈ interval / ln(1/0.9) ≈ 9.49 × interval;EF 越高衰减越慢。
   新卡没有 interval,给一个很小的 S,曲线会陡降 —— 这正是「不复习就忘」的直观样子。 */
const W = 620
const H = 130
const DAYS = 30

const curve = computed(() => {
  const c = card.value
  const interval = c?.interval || 0
  const ef = c?.ease_factor || 2.5
  /* 新卡没有 interval,S 给 1.2 让曲线在 30 天窗口里真能看出衰减;
     取 0 会让整条曲线贴死底边,看上去像渲染坏了。 */
  const S = interval > 0 ? 9.49 * interval * (ef / 2.5) : 1.2

  const pts = []
  for (let d = 0; d <= DAYS; d += 0.5) {
    const r = Math.exp(-d / S)
    pts.push(`${((d / DAYS) * W).toFixed(1)},${(H - r * H).toFixed(1)}`)
  }

  const dueDay = c?.days_until_due ?? 0
  const showDot = interval > 0 && dueDay <= DAYS
  return {
    path: `M${pts.join(' L')}`,
    s: S,
    due: showDot
      ? { x: (dueDay / DAYS) * W, y: H - Math.exp(-dueDay / S) * H }
      : null,
    /* 今天的位置(曲线起点)保留率恒为 1 */
    now: { x: 0, y: 0 },
  }
})

/* by_day 是稀疏字典,补齐 7 天才能画成等距柱 */
/* 不到一天时说「几小时后」比说「0 天后」有用得多 */
const dueText = computed(() => {
  const c = card.value
  if (!c || !c.interval) return '新卡:曲线是「不复习就忘」的示意'
  const h = c.due_in_hours || 0
  const when = h <= 0 ? '已到期' : h < 24 ? `${Math.round(h)} 小时后` : `${c.days_until_due} 天后`
  return `S≈${curve.value.s.toFixed(1)} 天 · 下次 ${when}`
})

const load = computed(() => {
  const by = st.value.load7?.by_day || {}
  return Array.from({ length: 7 }, (_, d) => ({ d, n: by[d] ?? by[String(d)] ?? 0 }))
})
const loadMax = computed(() => Math.max(1, ...load.value.map((x) => x.n)))

async function grade(g) {
  err.value = ''
  try {
    await gradeCard(g)
  } catch (e) {
    err.value = String(e?.message || e)
  }
}
</script>

<template>
  <PanelBox
    title="复习"
    tag="SM-2"
    :state="rv.state"
    :ready="rv.state === 'ok'"
    @refresh="refreshPanel('review')"
  >
    <div class="pn-kpi">
      <div><b>{{ st.cards }}</b><span>复习卡</span></div>
      <div><b>{{ st.due }}</b><span>今日到期</span></div>
      <div><b>{{ st.fresh }}</b><span>新卡</span></div>
      <div><b>{{ masteryPct }}%</b><span>平均掌握</span></div>
    </div>

    <div class="rv-grid">
      <section>
        <div class="pn-h">
          学习卡
          <em v-if="rv.session === 'running'">{{ pos }} / {{ total }}</em>
          <em v-else>队列 {{ total }} 张</em>
        </div>

        <div v-if="rv.session === 'running' && card" class="card">
          <div class="ct">
            <span class="pn-tag">{{ card.ku_type }}</span>
            <span class="dim">{{ card.domain || '未分域' }}</span>
            <span v-if="card.is_new" class="pn-tag new">新卡</span>
            <span v-else class="dim">第 {{ (card.repetitions || 0) + 1 }} 次</span>
          </div>

          <h3>{{ card.name }}</h3>

          <template v-if="rv.revealed">
            <p v-if="card.summary && card.summary !== card.name" class="sum">{{ card.summary }}</p>
            <p v-if="card.text" class="src">{{ card.text }}</p>
            <div class="grades">
              <button class="g g0" @click="grade(0)">忘记<span>重新开始</span></button>
              <button class="g g3" @click="grade(3)">模糊<span>间隔不涨</span></button>
              <button class="g g4" @click="grade(4)">记得<span>正常推进</span></button>
              <button class="g g5" @click="grade(5)">轻松<span>EF +0.1</span></button>
            </div>
          </template>
          <template v-else>
            <p class="hint">先在心里回想一遍,再显示答案。凭印象评分,不要凭「刚才看过」。</p>
            <button class="act" @click="revealCard()">显示答案</button>
          </template>
          <p v-if="err" class="err">{{ err }}</p>
        </div>

        <div v-else-if="rv.session === 'done'" class="done">
          <b>这一轮过完了</b>
          <p v-if="rv.lastMsg">{{ rv.lastMsg }}</p>
          <p class="dim">下一轮按 SM-2 排期,到期的会重新出现在这里。</p>
          <button class="act" @click="startReview()">再来一轮</button>
        </div>

        <div v-else class="idle">
          <p v-if="todo">
            今日待办:<b>{{ st.due }}</b> 张到期 + <b>{{ st.fresh }}</b> 张新卡
          </p>
          <p v-else-if="!st.cards" class="dim">
            还没有复习卡。在右栏上部或学习库录一句知识,抽取出的知识单元会自动变成卡。
          </p>
          <p v-else class="dim">今天的卡都过完了。明天再来,间隔会按 SM-2 自然拉长。</p>
          <button v-if="todo" class="act" @click="startReview()">
            开始复习 {{ Math.min(todo, 20) }} 张
          </button>
        </div>
      </section>

      <aside class="side">
        <div class="pn-h">遗忘曲线 <em>R = e^(−t/S)</em></div>
        <svg class="curve" :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="none">
          <line class="ref" x1="0" :y1="H * 0.1" :x2="W" :y2="H * 0.1" />
          <path class="line" :d="curve.path" />
          <circle v-if="curve.due" class="dot" :cx="curve.due.x" :cy="curve.due.y" r="4" />
        </svg>
        <div class="axis">
          <span>今天</span>
          <span class="dim">{{ dueText }}</span>
          <span>+{{ DAYS }} 天</span>
        </div>
        <p class="note">
          排期规则:答「忘记」重置为 1 天;答「记得 / 轻松」按 1 → 6 → 16 天递进,
          EF 每次 ±0.1 微调,间隔随熟练度拉长。
        </p>
        <p class="note warn">
          进度存在知识单元自身,和知识星球共用同一个库。所以在星球里「按来源删除」删掉的
          卡片,进度会一起消失;清库接口会清空全部知识与进度。
        </p>

        <div class="pn-h" style="margin-top: 16px">
          未来 7 天 <em>{{ st.load7?.total_due || 0 }} 张</em>
        </div>
        <div class="load">
          <div v-for="x in load" :key="x.d" class="bar">
            <i :style="{ height: `${Math.max(2, (x.n / loadMax) * 34)}px` }" :class="{ zero: !x.n }" />
            <span>{{ x.d === 0 ? '今' : `+${x.d}` }}</span>
            <em>{{ x.n }}</em>
          </div>
        </div>

        <template v-if="st.risk?.length">
          <div class="pn-h" style="margin-top: 16px">遗忘风险</div>
          <button
            v-for="r in st.risk"
            :key="r.item_id"
            class="pn-row clickable"
            @click="openKu(r.item_id)"
          >
            <span class="m">
              <b>{{ r.name || r.item_id }}</b>
              <span class="s">{{ r.reason }}</span>
            </span>
          </button>
        </template>
      </aside>
    </div>
  </PanelBox>
</template>

<style scoped>
.rv-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr);
  gap: 22px;
  align-items: start;
}
/* 断点看面板容器宽度,不是视口 —— 中栏只占窗口一半,
   1600 的窗口里也只有 ~580px,按视口判断会永远两列挤成两条窄条。 */
@container (max-width: 820px) {
  .rv-grid { grid-template-columns: minmax(0, 1fr); gap: 20px; }
}

/* ── 卡片 ── */
.card {
  padding: 16px 18px 18px;
  border-radius: var(--r-md);
  background: rgba(255, 255, 255, 0.86);
  border: 1px solid var(--line-soft);
  box-shadow: 0 1px 3px rgba(23, 32, 26, 0.04);
}
.ct { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.ct .dim { font-size: 11.5px; color: var(--dim); }
.pn-tag.new { background: rgba(26, 157, 95, 0.12); color: var(--green); }
.card h3 {
  font-family: var(--serif);
  font-size: 17px; line-height: 1.62; font-weight: 600;
  color: var(--ink); margin-bottom: 12px;
}
.sum { font-size: 13px; line-height: 1.8; color: var(--dim); margin-bottom: 10px; }
.src {
  font-size: 12px; line-height: 1.85; color: var(--dim);
  padding: 9px 12px; border-left: 2px solid var(--line);
  background: rgba(23, 32, 26, 0.025);
  margin-bottom: 14px; white-space: pre-wrap;
}
.hint { font-size: 12px; color: var(--dim); margin: 4px 0 14px; line-height: 1.7; }
.err { margin-top: 10px; font-size: 11px; color: #b4453c; }

.act {
  display: inline-block;
  padding: 8px 18px; border-radius: var(--r-pill);
  font-size: 12px; font-weight: 600; color: #fff;
  background: var(--green);
  transition: opacity 0.15s;
}
.act:hover { opacity: 0.9; }

.grades { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 7px; }
.g {
  display: flex; flex-direction: column; align-items: center; gap: 2px;
  padding: 9px 4px 8px; border-radius: var(--r-sm);
  font-size: 12px; font-weight: 600; color: var(--ink);
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid var(--line-soft);
  transition: border-color 0.14s, background 0.14s, color 0.14s;
}
.g span { font-size: 11px; font-weight: 500; color: var(--dim); }
.g:hover { border-color: rgba(26, 157, 95, 0.42); background: rgba(26, 157, 95, 0.06); }
.g0:hover { border-color: rgba(180, 69, 60, 0.42); background: rgba(180, 69, 60, 0.06); }

.done, .idle { padding: 14px 2px; }
.done b { font-size: 13px; }
.done p, .idle p { font-size: 12px; line-height: 1.8; color: var(--dim); margin: 6px 0 14px; }
.idle b { color: var(--ink); }

/* ── 曲线 ── */
.curve {
  display: block; width: 100%; height: 130px;
  overflow: visible;
}
.curve .ref { stroke: rgba(23, 32, 26, 0.12); stroke-width: 1; stroke-dasharray: 3 3; vector-effect: non-scaling-stroke; }
.curve .line { fill: none; stroke: var(--green); stroke-width: 1.8; vector-effect: non-scaling-stroke; }
.curve .dot { fill: var(--green); stroke: #fff; stroke-width: 1.5; vector-effect: non-scaling-stroke; }
.axis {
  display: flex; justify-content: space-between; align-items: baseline;
  margin-top: 5px; font-size: 11px; font-weight: 600; color: var(--dim);
}
.axis .dim { font-family: var(--mono); font-size: 11px; }
.note {
  margin-top: 8px; font-size: 11.5px; line-height: 1.75; color: var(--dim);
}
.note.warn {
  padding: 7px 9px; border-radius: var(--r-sm);
  background: rgba(196, 138, 26, 0.07);
  border-left: 2px solid rgba(196, 138, 26, 0.45);
  color: #8a6a24;
}

/* ── 7 天负荷 ── */
.load { display: flex; align-items: flex-end; gap: 6px; padding: 8px 2px 0; }
.load .bar {
  flex: 1; display: flex; flex-direction: column; align-items: center; gap: 3px;
  min-width: 0;
}
.load i {
  display: block; width: 100%; max-width: 26px; border-radius: 2px 2px 0 0;
  background: rgba(26, 157, 95, 0.55);
}
.load i.zero { background: rgba(23, 32, 26, 0.14); }
.load span { font-size: 10.5px; color: var(--dim); }
.load em { font-style: normal; font-family: var(--mono); font-size: 10.5px; font-weight: 600; color: var(--ink); }
</style>
