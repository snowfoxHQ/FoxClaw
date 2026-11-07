<script setup>
import { computed, onMounted, ref } from 'vue'
import MenuPopover from './MenuPopover.vue'
import { state, loadSessions, openSession, moduleLabel, setModule } from '../stores/app.js'
import { loadSession, newSession } from '../stores/chat.js'

const btnRef = ref(null)
const menuOpen = ref(false)
const anchor = ref(null)

const count = computed(() => state.sessions.length)
const currentLabel = computed(() =>
  state.module === 'overview' ? '菜单' : `菜单 · ${moduleLabel(state.module)}`
)

onMounted(loadSessions)

function toggleMenu() {
  if (menuOpen.value) {
    menuOpen.value = false
    return
  }
  const r = btnRef.value?.getBoundingClientRect()
  /* 传 top 而不是 bottom:按钮贴着栏底,浮层必须从按钮上沿往上长,否则会盖住按钮自己 */
  if (r) anchor.value = { left: r.left, top: r.top }
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

    <button
      ref="btnRef"
      class="menu-btn"
      :class="{ on: menuOpen }"
      :aria-expanded="menuOpen"
      @click="toggleMenu"
    >
      <span class="ic">
        <svg viewBox="0 0 24 24"><path d="M4 6h16" /><path d="M4 12h16" /><path d="M4 18h16" /></svg>
      </span>
      <b>{{ currentLabel }}</b>
      <span class="caret">▴</span>
    </button>

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

.menu-btn {
  display: flex; align-items: center; gap: 8px; flex-shrink: 0;
  padding: 9px 10px; border-radius: var(--r-sm);
  background: rgba(255, 255, 255, 0.7);
  border: 1px solid var(--line-soft);
  transition: background 0.15s, border-color 0.15s;
}
.menu-btn:hover, .menu-btn.on { background: #fff; border-color: var(--line); }
.menu-btn .ic { width: 15px; height: 15px; display: grid; place-items: center; color: var(--dim); }
.menu-btn .ic svg { width: 15px; height: 15px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; }
.menu-btn b { font-size: 12.5px; font-weight: 600; flex: 1; text-align: left; }
.menu-btn .caret { font-size: 9px; color: var(--faint); transition: transform 0.18s; }
.menu-btn.on .caret { transform: rotate(180deg); }
</style>
