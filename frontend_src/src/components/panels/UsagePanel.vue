<script setup>
import { computed, onMounted, ref } from 'vue'
import PanelBox from './PanelBox.vue'
import { panels, loadPanel, refreshPanel, openTokenReport } from '../../stores/panels.js'
import { fmtNum, fmtMs, clip } from '../../lib/fmt.js'

const p = panels.usage
const err = ref('')
onMounted(() => loadPanel('usage'))

const ready = computed(() => !!p.tokens || Object.keys(p.gateway || {}).length > 0)
const sum = computed(() => p.tokens?.summary || {})
const agents = computed(() => Object.entries(p.tokens?.by_agent || {}))
const provs = computed(() => Object.entries(p.tokens?.by_provider || {}))
const llm = computed(() => Object.entries(p.metrics?.llm || {}))
const uptimeH = computed(() => Math.round((p.metrics?.uptime_s || 0) / 3600))

async function report() {
  err.value = ''
  try { await openTokenReport() } catch (e) { err.value = String(e.message || e) }
}
</script>

<template>
  <PanelBox
    title="使用情况"
    tag="Usage"
    :state="p.state"
    :ready="ready"
    @refresh="refreshPanel('usage')"
  >
    <div class="pn-kpi">
      <div><b>{{ fmtNum(sum.total_tokens ?? 0) }}</b><span>总 token</span></div>
      <div><b>{{ fmtNum(sum.calls ?? 0) }}</b><span>调用次数</span></div>
      <div><b>{{ fmtNum(Math.round(sum.avg_tokens_per_call ?? 0)) }}</b><span>平均 / 次</span></div>
      <div><b>{{ fmtNum(p.gateway.rejected_total ?? 0) }}</b><span>网关拒绝</span></div>
      <div><b>{{ uptimeH }}h</b><span>已运行</span></div>
    </div>

    <div v-if="err" class="pn-st err">{{ err }}</div>

    <div class="pn-cols">
      <div class="pn-sec">
        <div class="pn-h">按 Agent <em>{{ agents.length }}</em></div>
        <div v-if="!agents.length" class="pn-empty">还没有 token 消耗记录。</div>
        <div v-for="[name, v] in agents" :key="name" class="pn-row">
          <span class="m">
            <b>{{ name }}</b>
            <span class="s">输入 {{ fmtNum(v.input_tokens) }} · 输出 {{ fmtNum(v.output_tokens) }}</span>
          </span>
          <span class="r">{{ fmtNum(v.total_tokens) }}</span>
        </div>
      </div>

      <div class="pn-sec">
        <div class="pn-h">按 provider <em>{{ provs.length }}</em></div>
        <div v-if="!provs.length" class="pn-empty">还没有 provider 记录。</div>
        <div v-for="[name, v] in provs" :key="name" class="pn-row">
          <span class="m">
            <b>{{ name }}</b>
            <span class="s">{{ v.calls }} 次调用</span>
          </span>
          <span class="r">{{ fmtNum(v.total_tokens) }}</span>
        </div>
        <div v-for="[name, v] in llm" :key="'llm-' + name" class="pn-row">
          <span class="m">
            <b>{{ name }} 延迟</b>
            <span class="s">p50 {{ fmtMs(v.p50_ms) }} · p95 {{ fmtMs(v.p95_ms) }} · 错误率 {{ Math.round((v.error_rate || 0) * 100) }}%</span>
          </span>
          <span class="r">{{ v.count }} 次</span>
        </div>
      </div>
    </div>

    <div class="pn-sec">
      <div class="pn-h">最慢端点 p95 <em>{{ p.slow.length }}</em></div>
      <div v-if="!p.slow.length" class="pn-empty">还没有端点耗时统计。</div>
      <div v-for="e in p.slow" :key="e.name" class="pn-row" :title="e.name">
        <span class="m">
          <b>{{ clip(e.name, 46) }}</b>
          <span class="s">{{ e.count }} 次 · 错误 {{ e.errors }} · 平均 {{ fmtMs(e.avg_ms) }}</span>
        </span>
        <span class="r">{{ fmtMs(e.p95_ms) }}</span>
      </div>
    </div>

    <div class="pn-sec acts">
      <button class="btn" @click="report">在中间舞台打开完整报告</button>
      <span class="hint">网关 {{ p.gateway.enabled ? '已启用' : '未启用' }} ·
        {{ p.gateway.rate_per_min ?? '—' }}/分 · 配额 {{ fmtNum(p.gateway.quota_per_hour ?? 0) }}/时
      </span>
    </div>
  </PanelBox>
</template>

<style scoped>
.acts { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.btn {
  padding: 7px 13px; border-radius: var(--r-sm);
  font-size: 11.5px; color: var(--green); font-weight: 600;
  background: rgba(26, 157, 95, 0.1); border: 1px solid rgba(26, 157, 95, 0.16);
}
.btn:hover { background: rgba(26, 157, 95, 0.16); }
.hint { font-size: 11.5px; color: var(--dim); }
</style>
