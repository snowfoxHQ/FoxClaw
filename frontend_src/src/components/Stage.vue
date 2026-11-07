<script setup>
import { computed } from 'vue'
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
import { chat } from '../stores/chat.js'
import { state, moduleLabel, closeDoc } from '../stores/app.js'

/* 菜单 8 项 → 真实面板。overview 是核心对话视图,不在这里。 */
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

/* 标签条只在打开文档时需要(文档 + 回到面板)。
   没文档时面板头部已经写着模块名,再挂一条同名 tab 就是重复。 */
const hasTabs = computed(() => !!state.doc)
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
          <div class="core-cap">问我任何事 · 主管 Agent 会按需调度</div>
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
.tab.doc .x { font-size: 10px; color: var(--faint); }
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
/* 没有标签条时不留缺口,四角全圆 */
.stage-body.bare { margin-top: 8px; border-radius: var(--r-mid); }

/* ── 核心:空态居中,有消息时整块交给消息流 ── */
.core { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.core-empty {
  flex: 1; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 16px;
  position: relative;
}
.core-cap { font-size: 11.5px; color: var(--faint); }
</style>
