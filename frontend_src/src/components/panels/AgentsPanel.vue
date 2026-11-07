<script setup>
import { computed, onMounted, ref } from 'vue'
import PanelBox from './PanelBox.vue'
import { panels, loadPanel, refreshPanel } from '../../stores/panels.js'

const p = panels.agents
const open = ref('')
onMounted(() => loadPanel('agents'))

const ready = computed(() => p.list.length > 0)
const shadowCount = computed(() => p.list.filter((a) => a.harness?.shadow_mode).length)
const nodes = computed(() => p.list.reduce((n, a) => n + (a.thought_chain?.total_nodes || 0), 0))
const blocked = computed(() => p.list.reduce((n, a) => n + (a.thought_chain?.blocked_nodes || 0), 0))

function toggle(id) { open.value = open.value === id ? '' : id }
</script>

<template>
  <PanelBox
    title="代理"
    tag="Agents"
    :state="p.state"
    :ready="ready"
    @refresh="refreshPanel('agents')"
  >
    <div class="pn-kpi">
      <div><b>{{ p.list.length }}</b><span>代理</span></div>
      <div><b>{{ shadowCount }}</b><span>影子模式</span></div>
      <div><b>{{ nodes }}</b><span>思考节点</span></div>
      <div><b>{{ blocked }}</b><span>被拦截</span></div>
    </div>

    <div class="pn-sec">
      <div class="pn-h">代理清单 <em>{{ p.list.length }}</em></div>
      <div v-if="!p.list.length" class="pn-empty">后端没有返回任何 Agent。</div>
      <div v-for="a in p.list" :key="a.agent_id" class="ag">
        <div class="pn-row clickable" @click="toggle(a.agent_id)">
          <span class="m">
            <b>{{ a.agent_name }} <em class="id">{{ a.agent_id }}</em></b>
            <span class="s">
              {{ a.thought_chain?.total_nodes ?? 0 }} 节点 ·
              置信度 {{ Math.round((a.thought_chain?.avg_confidence ?? 0) * 100) }}% ·
              规则 {{ a.harness?.policy?.total_rules ?? 0 }} 条
            </span>
          </span>
          <span class="pn-tag" :class="a.harness?.shadow_mode ? 'amber' : 'green'">
            {{ a.harness?.shadow_mode ? '影子' : '在线' }}
          </span>
        </div>

        <div v-if="open === a.agent_id" class="det">
          <div class="kv"><span>拦截阈值</span><b>
            warn {{ a.harness?.thresholds?.warn ?? '—' }} / block {{ a.harness?.thresholds?.block ?? '—' }}
          </b></div>
          <div class="kv"><span>漂移检测</span><b>{{ a.harness?.thresholds?.embedding_mode || '—' }}</b></div>
          <div class="kv"><span>语义维度</span><b>
            {{ a.harness?.thresholds?.semantic_degraded ? '已降级(仅 tool/scope 两维)' : '正常' }}
          </b></div>
          <div class="kv"><span>阻塞 / 警告</span><b>
            {{ a.thought_chain?.blocked_nodes ?? 0 }} / {{ a.thought_chain?.warned_nodes ?? 0 }}
          </b></div>
          <div class="kv"><span>影子轮次</span><b>
            {{ a.harness?.thresholds?.run_count ?? 0 }} / {{ a.harness?.thresholds?.shadow_rounds ?? '—' }}
          </b></div>
        </div>
      </div>
    </div>

    <div class="pn-note">
      <template v-if="p.info">
        版本 <code>{{ p.info.version }}</code> · 数据目录 <code>{{ p.info.data_dir }}</code>
      </template>
      数据来自 <code>/api/status</code> 与 <code>/api/system/info</code>。
    </div>
  </PanelBox>
</template>

<style scoped>
.id { font-family: var(--mono); font-size: 11px; font-style: normal; color: var(--dim); margin-left: 4px; }
.det {
  margin: 2px 0 8px 10px; padding: 8px 12px;
  border-left: 2px solid var(--line);
}
.kv { display: flex; justify-content: space-between; gap: 12px; padding: 4px 0; font-size: 12px; color: var(--dim); }
.kv b { font-weight: 600; color: var(--ink); font-family: var(--mono); font-size: 11.5px; text-align: right; }
</style>
