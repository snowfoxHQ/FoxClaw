<script setup>
import { computed, onMounted } from 'vue'
import PanelBox from './PanelBox.vue'
import { panels, loadPanel, refreshPanel } from '../../stores/panels.js'
import { fmtAgo, fmtMs, clip } from '../../lib/fmt.js'

const p = panels.activity
onMounted(() => loadPanel('activity'))

const ready = computed(() => p.events.length > 0 || p.traces.length > 0 || !!p.thought)
const events = computed(() => p.events.slice(0, 14))
const slow = computed(() => p.traces.slice(0, 8))

function evText(e) {
  const pl = e.payload || {}
  return pl.task || pl.reason || pl.phase || pl.agent_id || e.topic || e.channel || '(空事件)'
}
function evSub(e) {
  const src = e.source ? `来源 ${e.source}` : ''
  return [e.channel, src].filter(Boolean).join(' · ')
}
</script>

<template>
  <PanelBox
    title="活动"
    tag="Activity"
    :state="p.state"
    :ready="ready"
    @refresh="refreshPanel('activity')"
  >
    <div class="pn-kpi">
      <div><b>{{ events.length }}</b><span>事件</span></div>
      <div><b>{{ p.traces.length }}</b><span>请求链路</span></div>
      <div><b>{{ p.thought?.total_nodes ?? 0 }}</b><span>思考节点</span></div>
      <div><b>{{ p.thought ? Math.round((p.thought.avg_confidence ?? 0) * 100) + '%' : '—' }}</b><span>平均置信度</span></div>
    </div>

    <div class="pn-cols">
      <div class="pn-sec">
        <div class="pn-h">事件总线 <em>{{ p.events.length }}</em></div>
        <div v-if="!events.length" class="pn-empty">
          还没有事件。发一条消息、让 Agent 跑一步,执行 → 审计 → 记忆的流转会记在这里。
        </div>
        <div v-for="(e, i) in events" :key="i" class="pn-row static" :title="evText(e)">
          <span class="m">
            <b>{{ clip(evText(e), 40) }}</b>
            <span class="s">{{ evSub(e) }}</span>
          </span>
          <span class="r">{{ fmtAgo(e.ts) }}</span>
        </div>
      </div>

      <div class="pn-sec">
        <div class="pn-h">最慢请求链路 <em>{{ p.traces.length }}</em></div>
        <div v-if="!slow.length" class="pn-empty">
          还没有链路记录。任何一次 API 调用都会留下 trace。
        </div>
        <div v-for="(t, i) in slow" :key="t.trace_id || i" class="pn-row" :title="t.trace_id">
          <span class="m">
            <b>{{ clip(t.name, 34) }}</b>
            <span class="s">{{ t.span_count || 0 }} span</span>
          </span>
          <span class="r">{{ fmtMs(t.duration_ms) }}</span>
        </div>
      </div>
    </div>

    <div class="pn-sec">
      <div class="pn-note">
        思考链健康度：共 {{ p.thought?.total_nodes ?? 0 }} 个节点,阻塞 {{ p.thought?.blocked_nodes ?? 0 }} 个,
        警告 {{ p.thought?.warned_nodes ?? 0 }} 个,阻塞率
        {{ p.thought ? Math.round((p.thought.block_rate ?? 0) * 100) + '%' : '—' }}。
        数据来自 <code>/api/bus/history</code>、<code>/api/traces</code>、<code>/api/thoughtchain/{agent}/summary</code>。
      </div>
    </div>
  </PanelBox>
</template>

<style scoped>
.pn-row.static:hover { background: transparent; }
</style>
