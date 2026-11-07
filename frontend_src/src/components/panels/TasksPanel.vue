<script setup>
import { computed, onMounted } from 'vue'
import PanelBox from './PanelBox.vue'
import { panels, loadPanel, refreshPanel } from '../../stores/panels.js'
import { fmtAgo, clip } from '../../lib/fmt.js'

const p = panels.tasks
onMounted(() => loadPanel('tasks'))

const ready = computed(() => p.state === 'ok' && Object.keys(p.sched).length > 0)
const byState = computed(() => p.procStats?.by_state || {})

/* 后端把每个任务建模成「进程」,状态机 created→queued→scheduled→running→completed/failed */
function stateTag(s) {
  return s === 'completed' ? 'green' : s === 'failed' ? 'red' : s === 'running' ? 'amber' : ''
}
const STATE_CN = {
  created: '已创建', queued: '排队中', scheduled: '已调度',
  running: '执行中', completed: '已完成', failed: '失败',
}
const cn = (s) => STATE_CN[s] || s
</script>

<template>
  <PanelBox
    title="任务"
    tag="Tasks"
    :state="p.state"
    :ready="ready"
    @refresh="refreshPanel('tasks')"
  >
    <div class="pn-kpi">
      <div><b>{{ p.sched.submitted ?? 0 }}</b><span>已提交</span></div>
      <div><b>{{ p.sched.running ?? 0 }}</b><span>执行中</span></div>
      <div><b>{{ p.sched.completed ?? 0 }}</b><span>已完成</span></div>
      <div><b>{{ p.sched.failed ?? 0 }}</b><span>失败</span></div>
      <div><b>{{ p.procStats?.active ?? 0 }}</b><span>活跃进程</span></div>
    </div>

    <div class="pn-sec">
      <div class="pn-h">进程生命周期 <em>{{ p.processes.length }}</em></div>
      <div v-if="!p.processes.length" class="pn-empty">
        还没有任务进程。任务是按「进程」记录的：提交后排入调度,执行完进入终态,
        每一步的 <code>state_history</code> 都能追溯。
      </div>
      <div v-for="r in p.processes" :key="r.process_id" class="pn-row">
        <span class="m">
          <b>{{ clip(r.task, 52) }}</b>
          <span class="s">
            {{ r.agent_id || '未分配' }}
            <template v-if="r.route_hint"> · {{ r.route_hint }}</template>
            · {{ (r.state_history || []).join(' → ') }}
          </span>
        </span>
        <span class="pn-tag" :class="stateTag(r.state)">{{ cn(r.state) }}</span>
        <span class="r">{{ fmtAgo(r.created_at) }}</span>
      </div>
    </div>

    <div class="pn-sec">
      <div class="pn-h">按状态分布</div>
      <div v-if="!Object.keys(byState).length" class="pn-empty">暂时没有进程状态。</div>
      <div v-else class="pn-chips">
        <span v-for="(n, s) in byState" :key="s" class="pn-chip">{{ cn(s) }} {{ n }}</span>
      </div>
    </div>

    <div class="pn-note">
      并发峰值 {{ p.sched.peak_running ?? 0 }} / 上限
      {{ p.sched.max_concurrency ?? '未设' }}。
      数据来自 <code>/api/scheduler/stats</code> 与 <code>/api/processes</code>。
    </div>
  </PanelBox>
</template>
