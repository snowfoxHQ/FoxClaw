<script setup>
import { computed, onMounted, ref } from 'vue'
import MenuPopover from './MenuPopover.vue'
import {
  state, loadSessions, openSession, moduleLabel, setModule, railItems, RAIL,
} from '../stores/app.js'
import { loadSession, newSession } from '../stores/chat.js'

const btnRef = ref(null)
const menuOpen = ref(false)
const anchor = ref(null)

const count = computed(() => state.sessions.length)
const rail = railItems()

/* 当前模块不属于常驻项 → 落在「更多」里。概览是默认落地页,不给它加后缀,否则
   一进站就显示「更多 · 概览」,像是从一个隐藏入口进来的。 */
const isMore = computed(() => !RAIL.includes(state.module))
const moreCur = computed(() => (isMore.value && state.module !== 'overview' ? moduleLabel(state.module) : ''))

/* 线性图标:统一 24 视框 + 描边,和右栏 / 顶栏的图形语言一致。
   只放 path 的 d,颜色与线宽交给 CSS,换肤时不用动 JS。 */
const ICONS = {
  book: [
    'M5 5.6A2.1 2.1 0 0 1 7.1 3.5H19v17H7.1A2.1 2.1 0 0 1 5 18.4z',
    'M5 18.4a2.1 2.1 0 0 1 2.1-2.1H19',
    'M9 8h6',
    'M9 11.2h4',
  ],
  clock: ['M12 3.2a8.8 8.8 0 1 0 0 17.6 8.8 8.8 0 0 0 0-17.6z', 'M12 7.4V12l3.3 1.9'],
  plug: ['M9 3v4.2', 'M15 3v4.2', 'M6.8 7.2h10.4v4.6a5.2 5.2 0 0 1-10.4 0z', 'M12 17v4'],
  bot: [
    'M4.8 8.2h14.4A1.8 1.8 0 0 1 21 10v6.6a1.8 1.8 0 0 1-1.8 1.8H4.8A1.8 1.8 0 0 1 3 16.6V10a1.8 1.8 0 0 1 1.8-1.8z',
    'M12 4.4v3.8',
    'M8.6 12.8h.01',
    'M15.4 12.8h.01',
  ],
  more: ['M6 12h.01', 'M12 12h.01', 'M18 12h.01'],
  review: [
    'M20.4 12a8.4 8.4 0 0 1-14.3 5.9',
    'M3.6 12a8.4 8.4 0 0 1 14.3-5.9',
    'M17.9 2.6v3.6h-3.6',
    'M6.1 21.4v-3.6h3.6',
  ],
  memory: ['M7.2 3.4h9.6v17.2L12 17.2l-4.8 3.4z'],
  dot: ['M12 10.6a1.4 1.4 0 1 0 0 2.8 1.4 1.4 0 0 0 0-2.8z'],
}

const icon = (name) => ICONS[name] || ICONS.dot
const quickOf = (m) => (m.quick || []).map((id) => {
  const q = { id, label: moduleLabel(id), icon: id }
  return q
})

onMounted(loadSessions)

function toggleMenu() {
  if (menuOpen.value) {
    menuOpen.value = false
    return
  }
  const r = btnRef.value?.getBoundingClientRect()
  /* 入口现在在栏顶,浮层必须往下长(原来的实现是贴着栏底往上长) */
  if (r) anchor.value = { left: r.left, bottom: r.bottom }
  menuOpen.value = true
}

function pick(id) {
  setModule(id)
  menuOpen.value = false
}

/* 切会话 = 切左侧高亮 + 拉后端历史消息 */
function pickSession(s) {
  openSession(s)
  loadSession(s.id)
}

async function startNew() {
  await newSession()
  setModule('overview')
}

function fmt(ts) {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleString('zh-CN', {
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

function meta(s) {
  return [fmt(s.updated_at), s.message_count ? `${s.message_count} 条` : ''].filter(Boolean).join(' · ')
}
</script>

<template>
  <aside class="col col--left rail-left">
    <button class="new-sess" title="开一个新会话" @click="startNew">＋ 新会话</button>

    <nav class="rail-nav">
      <div
        v-for="m in rail"
        :key="m.id"
        class="rail-row"
        :class="{ on: state.module === m.id, sub: (m.quick || []).includes(state.module) }"
      >
        <button class="rail-item" @click="pick(m.id)">
          <span class="ic">
            <svg viewBox="0 0 24 24"><path v-for="(d, i) in icon(m.icon)" :key="i" :d="d" /></svg>
          </span>
          <b>{{ m.label }}</b>
          <span v-if="m.beta" class="beta">BETA</span>
        </button>

        <span v-if="m.quick" class="rail-acts">
          <button
            v-for="q in quickOf(m)"
            :key="q.id"
            class="act"
            :title="`打开${q.label}`"
            :aria-label="q.label"
            @click.stop="pick(q.id)"
          >
            <svg viewBox="0 0 24 24"><path v-for="(d, i) in icon(q.icon)" :key="i" :d="d" /></svg>
          </button>
        </span>
      </div>

      <div class="rail-row" :class="{ on: isMore }">
        <button
          ref="btnRef"
          class="rail-item rail-anchor"
          :aria-expanded="menuOpen"
          title="其它功能"
          @click="toggleMenu"
        >
          <span class="ic">
            <svg viewBox="0 0 24 24"><path v-for="(d, i) in icon('more')" :key="i" :d="d" /></svg>
          </span>
          <b>更多</b>
          <span v-if="moreCur" class="cur">{{ moreCur }}</span>
          <span class="caret" :class="{ open: menuOpen }">▾</span>
        </button>
      </div>
    </nav>

    <div class="sec-head">
      会话历史 <em>{{ count }}</em>
    </div>

    <div class="ns-list scroll">
      <div v-if="state.sessionsState === 'loading'" class="empty">加载中…</div>
      <div v-else-if="state.sessionsState === 'error'" class="empty">后端未连接</div>
      <div v-else-if="!count" class="empty">还没有会话 · 发一条消息即开始</div>
      <template v-else>
        <button
          v-for="s in state.sessions"
          :key="s.id"
          class="ns-item"
          :class="{ on: s.id === state.sessionId }"
          :title="s.title || '新会话'"
          @click="pickSession(s)"
        >
          <b>{{ s.title || '新会话' }}</b>
          <span>{{ meta(s) }}</span>
        </button>
      </template>
    </div>

    <MenuPopover
      v-if="menuOpen"
      :anchor="anchor"
      :active="state.module"
      @pick="pick"
      @close="menuOpen = false"
    />
  </aside>
</template>

<style scoped>
.rail-left { padding: 12px; gap: 10px; }

.new-sess {
  display: flex; align-items: center; justify-content: center; gap: 6px;
  padding: 9px 12px; flex-shrink: 0;
  font-size: 13px; font-weight: 600; color: var(--green);
  background: rgba(26, 157, 95, 0.1);
  border-radius: var(--r-sm);
  transition: background 0.16s, transform 0.1s;
}
.new-sess:hover { background: rgba(26, 157, 95, 0.17); }
.new-sess:active { transform: scale(0.985); }

/* ── 常驻功能入口 ── */
.rail-nav { display: flex; flex-direction: column; gap: 2px; flex-shrink: 0; }

.rail-row { position: relative; display: flex; align-items: center; }

.rail-item {
  flex: 1; min-width: 0;
  display: flex; align-items: center; gap: 9px;
  padding: 7px 9px; border-radius: var(--r-sm);
  border: 1px solid transparent;
  transition: background 0.14s, border-color 0.14s;
}
.rail-item .ic { width: 15px; height: 15px; display: grid; place-items: center; color: var(--dim); flex-shrink: 0; }
.rail-item .ic svg {
  width: 15px; height: 15px;
  fill: none; stroke: currentColor; stroke-width: 1.7;
  stroke-linecap: round; stroke-linejoin: round;
}
.rail-item b {
  flex: 1; min-width: 0; text-align: left;
  font-size: 12.5px; font-weight: 500; color: var(--ink);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.rail-row:hover .rail-item { background: rgba(255, 255, 255, 0.6); }
.rail-row.on .rail-item {
  background: rgba(255, 255, 255, 0.92);
  border-color: var(--line);
  box-shadow: 0 1px 2px rgba(23, 32, 26, 0.05);
}
.rail-row.on .rail-item .ic { color: var(--green); }
.rail-row.on .rail-item b { font-weight: 600; }

/* 停在子视图(复习 / 记忆中心)时,父项给一层更淡的高亮:既说明「你在这条线上」,
   又不和真正选中的白卡混淆。 */
.rail-row.sub .rail-item { background: rgba(255, 255, 255, 0.5); }
.rail-row.sub .rail-item .ic { color: var(--green); }

/* 成熟度标签:比正式入口新,或者语义还在变 */
.beta {
  flex-shrink: 0;
  font-size: 8.5px; font-weight: 700; letter-spacing: 0.08em;
  color: var(--amber);
  padding: 1px 4px; border-radius: 4px;
  background: rgba(199, 116, 0, 0.1);
}

/* 同源数据的另一视图:只在 hover / 聚焦时出现,不占常驻视觉 */
.rail-acts {
  position: absolute; right: 5px; top: 50%; transform: translateY(-50%);
  display: flex; gap: 2px;
  padding-left: 14px;
  background: linear-gradient(90deg, rgba(255, 255, 255, 0) 0%, rgba(255, 255, 255, 0.94) 34%);
  opacity: 0;
  transition: opacity 0.13s;
  pointer-events: none;
}
.rail-row:hover .rail-acts,
.rail-acts:focus-within { opacity: 1; pointer-events: auto; }
.act {
  width: 21px; height: 21px; display: grid; place-items: center;
  border-radius: 6px; color: var(--dim);
  transition: background 0.13s, color 0.13s;
}
.act svg {
  width: 13px; height: 13px;
  fill: none; stroke: currentColor; stroke-width: 1.8;
  stroke-linecap: round; stroke-linejoin: round;
}
.act:hover { background: rgba(26, 157, 95, 0.12); color: var(--green); }

.cur { font-size: 10.5px; color: var(--faint); flex-shrink: 0; }
.caret { font-size: 9px; color: var(--faint); transition: transform 0.18s; flex-shrink: 0; }
.caret.open { transform: rotate(180deg); }

.sec-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 2px 4px 0;
  font-size: 11px; font-weight: 600; letter-spacing: 0.06em;
  color: var(--faint); text-transform: uppercase;
  flex-shrink: 0;
}
.sec-head em {
  font-size: 10.5px; color: var(--dim);
  padding: 1px 6px; border-radius: var(--r-pill);
  background: rgba(255, 255, 255, 0.8); border: 1px solid var(--line-soft);
}

.ns-list { display: flex; flex-direction: column; gap: 3px; flex: 1; margin: 0 -4px; padding: 0 4px; }
.ns-item {
  text-align: left;
  display: flex; flex-direction: column; gap: 2px;
  padding: 8px 10px; border-radius: var(--r-sm);
  border: 1px solid transparent;
  transition: background 0.14s, border-color 0.14s;
}
.ns-item b {
  font-size: 12.5px; font-weight: 500; color: var(--ink);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.ns-item span { font-size: 10.5px; color: var(--faint); }
.ns-item:hover { background: rgba(255, 255, 255, 0.66); }
.ns-item.on {
  background: rgba(255, 255, 255, 0.92);
  border-color: var(--line);
  box-shadow: 0 1px 2px rgba(23, 32, 26, 0.05);
}
</style>
