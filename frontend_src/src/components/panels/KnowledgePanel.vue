<script setup>
import { computed, onMounted } from 'vue'
import PanelBox from './PanelBox.vue'
import { panels, loadPanel, refreshPanel } from '../../stores/panels.js'
import { fmtNum, clip } from '../../lib/fmt.js'

const p = panels.knowledge
onMounted(() => loadPanel('knowledge'))

const ready = computed(() => Object.keys(p.stats || {}).length > 0)
const idx = computed(() => p.index || {})
const kus = computed(() => Object.entries(p.stats?.ku_counts || {}))
</script>

<template>
  <PanelBox
    title="知识库"
    tag="Knowledge"
    :state="p.state"
    :ready="ready"
    @refresh="refreshPanel('knowledge')"
  >
    <div class="pn-kpi">
      <div><b>{{ fmtNum(p.stats.total_kus ?? 0) }}</b><span>知识单元</span></div>
      <div><b>{{ fmtNum(p.stats.total_relations ?? 0) }}</b><span>关系</span></div>
      <div><b>{{ fmtNum(p.stats.graph_nodes ?? 0) }}</b><span>图谱节点</span></div>
      <div><b>{{ fmtNum(p.stats.open_conflicts ?? 0) }}</b><span>待裁决冲突</span></div>
      <div><b>{{ fmtNum(p.stats.vector_size ?? 0) }}</b><span>向量条目</span></div>
    </div>

    <div class="pn-cols">
      <div class="pn-sec">
        <div class="pn-h">索引状态 <em>{{ idx.index_state || '—' }}</em></div>
        <div class="pn-row">
          <span class="m">
            <b>{{ idx.index_state === 'ready' ? '索引就绪' : '索引' + (idx.index_state || '未知') }}</b>
            <span class="s">
              扫描 {{ fmtNum(idx.scanned ?? 0) }} · 就绪 {{ fmtNum(idx.ready ?? 0) }} ·
              待处理 {{ fmtNum(idx.pending ?? 0) }} · 失败 {{ fmtNum(idx.failed ?? 0) }}
            </span>
          </span>
          <span class="pn-tag" :class="idx.index_state === 'ready' ? 'green' : 'amber'">
            {{ idx.index_version || 'v—' }}
          </span>
        </div>
        <div class="pn-note meta">
          向量模型 <code>{{ p.stats.embedding || '未知' }}</code><br />
          BM25 {{ fmtNum(p.stats.bm25_size ?? 0) }} · 向量 {{ fmtNum(p.stats.vector_size ?? 0) }}<br />
          数据目录 <code>{{ p.stats.data_dir || '—' }}</code>
        </div>
      </div>

      <div class="pn-sec">
        <div class="pn-h">按知识域 <em>{{ kus.length }}</em></div>
        <div v-if="!kus.length" class="pn-empty">摄入资料后,这里按知识域统计条目数。</div>
        <div v-for="[k, n] in kus" :key="k" class="pn-row">
          <span class="m"><b>{{ k }}</b></span>
          <span class="r">{{ n }}</span>
        </div>

        <div class="pn-h sec-gap">已摄入资料 <em>{{ p.sources.length }}</em></div>
        <div v-if="!p.sources.length" class="pn-empty">
          知识库还是空的。把 PDF / Markdown / 文本拖进输入框(或走摄入接口),资料会出现在这里。
        </div>
        <div v-for="(s, i) in p.sources" :key="i" class="pn-row" :title="s.name || s.source">
          <span class="m">
            <b>{{ clip(s.name || s.source || '未命名资料', 34) }}</b>
            <span class="s">{{ s.year || '年份未知' }}</span>
          </span>
        </div>
      </div>
    </div>

    <div class="pn-sec">
      <div class="pn-h">能力清单 <em>{{ p.abilities.length }}</em></div>
      <div v-if="!p.abilities.length" class="pn-empty">后端没有返回能力清单。</div>
      <div v-else class="pn-chips">
        <span v-for="a in p.abilities" :key="a" class="pn-chip">{{ a }}</span>
      </div>
    </div>

    <div class="pn-note">
      数据来自 HKC 根路径 <code>/stats</code>、<code>/knowledge/sources</code>,
      加 Snowfoxclaw 侧 <code>/api/knowledge/abilities</code>。
    </div>
  </PanelBox>
</template>

<style scoped>
.meta { margin-top: 8px; }
.sec-gap { margin-top: 14px; }
</style>
