<script setup>
import { computed, onMounted, ref } from 'vue'
import PanelBox from './PanelBox.vue'
import { panels, loadPanel, refreshPanel, dropPreference } from '../../stores/panels.js'
import { openDoc } from '../../stores/app.js'
import { fmtTime, fmtNum, clip } from '../../lib/fmt.js'

const p = panels.memory
const err = ref('')
const busy = ref('')
onMounted(() => loadPanel('memory'))

const ready = computed(() => !!p.stats.backend)
const byAgent = computed(() => Object.entries(p.stats.by_agent || {}))

/* 记忆没有「单条详情」端点,但 recent 已经带了 summary 全文 —— 直接进正文皮肤,不再打一次后端 */
function open(m) {
  openDoc({
    id: `mem:${m.id}`,
    kind: 'text',
    lang: 'markdown',
    title: m.title || m.id,
    path: `memory/${m.id}.md`,
    meta: `${m.type || '记忆'} · ${m.agent || '—'} · ${fmtTime(m.created_at)}`,
    content: m.summary || '(无摘要)',
  })
}

async function drop(pref) {
  busy.value = pref.id
  err.value = ''
  try { await dropPreference(pref.id) } catch (e) { err.value = String(e.message || e) }
  busy.value = ''
}
</script>

<template>
  <PanelBox
    title="记忆中心"
    tag="Memory"
    :state="p.state"
    :ready="ready"
    @refresh="refreshPanel('memory')"
  >
    <div class="pn-kpi">
      <div><b>{{ fmtNum(p.stats.total_memories ?? 0) }}</b><span>记忆条目</span></div>
      <div><b>{{ fmtNum(p.stats.relations ?? 0) }}</b><span>关联</span></div>
      <div><b>{{ fmtNum(p.stats.shared ?? 0) }}</b><span>共享</span></div>
      <div><b>{{ fmtNum(p.stats.private ?? 0) }}</b><span>私有</span></div>
      <div><b>{{ fmtNum(p.prefs.length) }}</b><span>用户偏好</span></div>
    </div>

    <div v-if="err" class="pn-st err">{{ err }}</div>

    <div class="pn-cols">
      <div class="pn-sec">
        <div class="pn-h">最近记忆 <em>{{ p.recent.length }}</em></div>
        <div v-if="!p.recent.length" class="pn-empty">
          还没有记忆。聊过的内容会被 HMR 提炼成记忆条目,这里能看到最新的几条。
        </div>
        <div
          v-for="m in p.recent"
          :key="m.id"
          class="pn-row clickable"
          :title="'点击看全文'"
          @click="open(m)"
        >
          <span class="m">
            <b>{{ clip(m.title || m.id, 40) }}</b>
            <span class="s">{{ m.type || '记忆' }} · {{ m.agent || '—' }}</span>
          </span>
          <span v-if="m.shared" class="pn-tag">共享</span>
          <span class="r">{{ fmtTime(m.created_at) }}</span>
        </div>
      </div>

      <div class="pn-sec">
        <div class="pn-h">按 Agent <em>{{ byAgent.length }}</em></div>
        <div v-if="!byAgent.length" class="pn-empty">没有按 Agent 的分布数据。</div>
        <div v-for="[a, n] in byAgent" :key="a" class="pn-row">
          <span class="m"><b>{{ a }}</b></span>
          <span class="r">{{ n }}</span>
        </div>

        <div class="pn-h sec-gap">用户偏好 <em>{{ p.prefs.length }}</em></div>
        <div v-if="!p.prefs.length" class="pn-empty">
          还没有记录偏好。你对 Agent 说过的长期设定会落在这里,误存的可以就地删掉。
        </div>
        <div v-for="pref in p.prefs" :key="pref.id" class="pn-row">
          <span class="m">
            <b>{{ clip(pref.content || pref.id, 40) }}</b>
            <span class="s">{{ pref.id }}</span>
          </span>
          <button class="del" :disabled="busy === pref.id" title="删除这条偏好" @click.stop="drop(pref)">
            删除
          </button>
        </div>
      </div>
    </div>

    <div class="pn-note">
      后端 <code>{{ p.stats.backend || '—' }}</code> v{{ p.stats.version || '—' }} ·
      向量 <code>{{ p.stats.embedding || '—' }}</code>。
      数据来自 <code>/api/memory/stats</code>、<code>/api/memory/recent</code>、<code>/api/preferences</code>。
    </div>
  </PanelBox>
</template>

<style scoped>
.sec-gap { margin-top: 14px; }
.del {
  flex-shrink: 0; padding: 4px 10px; border-radius: var(--r-pill);
  font-size: 11px; font-weight: 600; color: var(--dim);
  background: var(--surface-2); border: 1px solid var(--line-soft);
}
.del:hover { color: var(--red); background: rgba(229, 72, 77, 0.1); }
.del:disabled { opacity: 0.5; }
</style>
