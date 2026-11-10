<script setup>
import { computed, onMounted, ref } from 'vue'
import DocViewer from './DocViewer.vue'
import MessageStream from './MessageStream.vue'
import ActivityPanel from './panels/ActivityPanel.vue'
import TasksPanel from './panels/TasksPanel.vue'
import AgentsPanel from './panels/AgentsPanel.vue'
import ToolsPanel from './panels/ToolsPanel.vue'
import UsagePanel from './panels/UsagePanel.vue'
import KnowledgePanel from './panels/KnowledgePanel.vue'
import ReviewPanel from './panels/ReviewPanel.vue'
import MemoryPanel from './panels/MemoryPanel.vue'
import SettingsPanel from './panels/SettingsPanel.vue'
import { chat, loadSession, say, send } from '../stores/chat.js'
import { state, moduleLabel, closeDoc, loadSessions, openSession } from '../stores/app.js'
import { profile, loadProfile, saveNickname } from '../stores/profile.js'
import { openKu } from '../stores/panels.js'
import { getJSON } from '../api.js'
import { clip } from '../lib/fmt.js'

const PANELS = {
  activity: ActivityPanel,
  tasks: TasksPanel,
  agents: AgentsPanel,
  tools: ToolsPanel,
  usage: UsagePanel,
  knowledge: KnowledgePanel,
  review: ReviewPanel,
  memory: MemoryPanel,
  settings: SettingsPanel,
}
const panel = computed(() => PANELS[state.module] || null)

const hasTabs = computed(() => !!state.doc)

const EXAMPLES = [
  '现在系统里有哪些技能可以用?',
  '帮我把今天要做的事排个优先级',
  '用一句话说清你能帮我做什么',
]

const recent = ref([])

const nick = ref('')
const nickSkipped = ref(localStorage.getItem('sfx.nick.skip') === '1')
const askNick = computed(() => profile.loaded && !profile.nickname && !nickSkipped.value)

onMounted(async () => {
  loadProfile().then(() => { nick.value = profile.nickname })
  if (!state.sessions.length) loadSessions().catch(() => {})
  const d = await getJSON('/knowledge/recent?limit=5').catch(() => null)
  recent.value = d?.kus || []
})

async function saveNick() {
  const n = nick.value.trim()
  if (!n) return
  try {
    await saveNickname(n)
    say(`记住了,以后称呼你为 ${n}`)
  } catch (e) {
    say(`保存失败:${e.message}`, 4000)
  }
}

function skipNick() {
  nickSkipped.value = true
  localStorage.setItem('sfx.nick.skip', '1')
}

const lastSession = computed(() => {
  const cur = chat.messages.length ? state.sessionId : ''
  return state.sessions.find((s) => s.id !== cur && (s.message_count || 0) > 0) || null
})

function resume() {
  const s = lastSession.value
  if (!s) return
  openSession(s)
  loadSession(s.id)
}
</script>

<template>
  <section class="stage">
    <div v-if="hasTabs" class="tabs">
      <button
        v-if="state.module !== 'overview'"
        class="tab"
        :class="{ on: !state.doc }"
        @click="closeDoc()"
      >
        {{ moduleLabel(state.module) }}
      </button>
      <button v-if="state.doc" class="tab on doc" @click="closeDoc()">
        <span class="t">{{ state.doc.title }}</span>
        <span class="x" title="关闭">✕</span>
      </button>
    </div>

    <div class="stage-body" :class="{ bare: !hasTabs }">
      <DocViewer v-if="state.doc" :doc="state.doc" />

      <component :is="panel" v-else-if="panel" :key="state.module" />

      <div v-else class="core">
        <MessageStream v-if="chat.messages.length" />
        <div v-else class="core-empty">
          <div class="hero">
            <div class="hero-t">问我任何事</div>
            <div class="core-cap">主管 Agent 会按需调度子 Agent;想让它读你的知识库,直接问就行</div>
          </div>

          <div v-if="askNick" class="ask-nick">
            <span class="an-q">我该怎么称呼你?</span>
            <input
              v-model="nick"
              class="an-in"
              maxlength="24"
              placeholder="例如:snow"
              @keydown.enter.prevent="saveNick"
            />
            <button class="an-ok" :disabled="profile.saving || !nick.trim()" @click="saveNick">就这么叫</button>
            <button class="an-skip" title="以后在设置里也能填" @click="skipNick">跳过</button>
          </div>

          <div class="tries">
            <button v-for="q in EXAMPLES" :key="q" class="try" @click="send(q)">
              <span class="arrow">→</span>{{ q }}
            </button>
          </div>

          <button v-if="lastSession" class="resume" @click="resume">
            <span class="rl">继续上次</span>
            <b>{{ clip(lastSession.title || '未命名会话', 28) }}</b>
            <span class="rm">{{ lastSession.message_count }} 条消息</span>
          </button>

          <div v-if="recent.length" class="recent">
            <div class="rh">最近知识</div>
            <div class="rk-list">
              <button v-for="k in recent" :key="k.ku_id" class="rk" :title="k.summary || k.name" @click="openKu(k.ku_id)">
                {{ clip(k.name || k.ku_id, 30) }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.stage { display: flex; flex-direction: column; min-height: 0; }

.tabs {
  display: flex; align-items: center; gap: 4px;
  padding: 8px 10px 0; flex-shrink: 0;
  overflow-x: auto;
}
.tab {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 11px; border-radius: var(--r-sm) var(--r-sm) 0 0;
  font-size: 12px; color: var(--dim);
  border: 1px solid transparent; border-bottom: 0;
  white-space: nowrap;
}
.tab:hover { color: var(--ink); background: rgba(255, 255, 255, 0.5); }
.tab.on {
  color: var(--ink); font-weight: 600;
  background: rgba(255, 255, 255, 0.86);
  border-color: var(--line-soft);
}
.tab.doc { max-width: 260px; }
.tab.doc .t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tab.doc .x { font-size: 11px; color: var(--dim); }
.tab.doc .x:hover { color: var(--red); }

.stage-body {
  flex: 1; min-height: 0;
  margin: 0 8px 8px;
  border-radius: 0 var(--r-mid) var(--r-mid) var(--r-mid);
  background: var(--mid-panel);
  border: 1px solid rgba(26, 157, 95, 0.1);
  overflow: hidden;
  display: flex;
}
.stage-body.bare { margin-top: 8px; border-radius: var(--r-mid); }

.core { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.core-empty {
  flex: 1; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 18px;
  position: relative;
  padding: 24px 18px;
  overflow-y: auto;
}
.core-cap { font-size: 12.5px; color: var(--dim); line-height: 1.7; text-align: center; }

.hero { text-align: center; }
.hero-t {
  font-size: 19px; font-weight: 600; letter-spacing: -0.02em;
  color: var(--ink); margin-bottom: 6px;
}

.ask-nick {
  display: flex; align-items: center; gap: 8px;
  width: 100%; max-width: 420px;
  padding: 9px 11px; border-radius: var(--r-sm);
  background: rgba(26, 157, 95, 0.06);
  border: 1px solid rgba(26, 157, 95, 0.22);
}
.an-q { font-size: 12.5px; font-weight: 600; color: var(--ink); flex-shrink: 0; }
.an-in {
  flex: 1; min-width: 56px;
  padding: 5px 8px; border-radius: 7px;
  font-size: 12.5px; color: var(--ink);
  background: #fff; border: 1px solid var(--line);
}
.an-in:focus { outline: none; border-color: rgba(26, 157, 95, 0.5); }
.an-ok {
  flex-shrink: 0;
  padding: 5px 10px; border-radius: 7px;
  font-size: 12px; font-weight: 600; color: #fff;
  background: var(--green);
}
.an-ok:disabled { opacity: 0.45; cursor: default; }
.an-skip { flex-shrink: 0; padding: 4px 2px; font-size: 11.5px; color: var(--dim); }
.an-skip:hover { color: var(--ink); }

.tries { display: flex; flex-direction: column; gap: 6px; width: 100%; max-width: 420px; }
.try {
  display: flex; align-items: center; gap: 8px; width: 100%;
  padding: 10px 13px; border-radius: var(--r-sm);
  font-size: 12.5px; color: var(--ink); text-align: left;
  background: rgba(255, 255, 255, 0.82);
  border: 1px solid var(--line);
  transition: color 0.14s, border-color 0.14s, background 0.14s, transform 0.12s;
}
.try:hover { color: var(--ink); background: #fff; border-color: rgba(26, 157, 95, 0.42); }
.try:active { transform: scale(0.99); }
.try .arrow { font-size: 12px; color: var(--green); flex-shrink: 0; }

.resume {
  display: flex; align-items: center; gap: 7px;
  max-width: 420px; padding: 7px 13px;
  border-radius: var(--r-pill);
  background: var(--surface-2); color: var(--dim);
  transition: background 0.14s, color 0.14s;
}
.resume:hover { background: var(--surface-3); color: var(--ink); }
.resume .rl { font-size: 11px; font-weight: 700; color: var(--dim); text-transform: uppercase; letter-spacing: 0.07em; }
.resume b {
  font-size: 12px; font-weight: 600;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.resume .rm { font-size: 11px; color: var(--dim); flex-shrink: 0; }

.recent { width: 100%; max-width: 420px; }
.recent .rh {
  margin-bottom: 5px;
  font-size: 11px; font-weight: 700; letter-spacing: 0.08em;
  color: var(--dim); text-transform: uppercase;
}
.rk-list { display: flex; flex-wrap: wrap; gap: 5px; }
.rk {
  max-width: 190px; padding: 5px 10px; border-radius: var(--r-pill);
  font-size: 11.5px; color: var(--ink);
  background: rgba(255, 255, 255, 0.8);
  border: 1px solid var(--line);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  transition: color 0.13s, border-color 0.13s, background 0.13s;
}
.rk:hover { color: var(--green); background: #fff; border-color: rgba(26, 157, 95, 0.4); }
</style>
