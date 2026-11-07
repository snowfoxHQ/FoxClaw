<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import PanelBox from './PanelBox.vue'
import { panels, loadPanel, refreshPanel, ingestKnowledge, openKu } from '../../stores/panels.js'
import { fmtNum, fmtTime, clip } from '../../lib/fmt.js'

const p = panels.knowledge
onMounted(() => loadPanel('knowledge'))

const draft = ref('')
const title = ref('')
const domain = ref('')

const ready = computed(() => Object.keys(p.stats || {}).length > 0)
const idx = computed(() => p.index || {})
const kus = computed(() => Object.entries(p.stats?.ku_counts || {}))
const busy = computed(() => p.ingest === 'busy')

/* 用户开始写新内容时,把上一次的「已收录 N 条」收起来,否则会让人以为这版也提交过了。
   只在**写入了非空内容**时清 —— 提交成功后代码会把 draft 置空,那不是用户输入,
   不能让它把刚拿到的结果提示擦掉。 */
watch(draft, (v) => {
  if (v && !busy.value) { p.ingest = 'idle'; p.ingestMsg = ''; p.ingestErr = '' }
})

async function submit() {
  if (!draft.value.trim() || busy.value) return
  try {
    await ingestKnowledge({ text: draft.value, title: title.value, domain: domain.value })
    draft.value = ''
    title.value = ''
  } catch {
    /* 错误已写进 store,模板里显示 */
  }
}
</script>

<template>
  <PanelBox
    title="知识库"
    tag="Knowledge"
    :state="p.state"
    :ready="ready"
    @refresh="refreshPanel('knowledge')"
  >
    <!-- 录入:短知识在这里手录;整本书 / 大文档走知识星球 -->
    <div class="kb-in">
      <textarea
        v-model="draft"
        rows="3"
        spellcheck="false"
        placeholder="写一句或一小段知识 —— 一句话也算。长文档、整本书走知识星球。"
        @keydown.ctrl.enter.prevent="submit"
      />
      <div class="kb-bar">
        <input v-model="title" class="f-title" placeholder="标题(可选)" />
        <input v-model="domain" class="f-domain" placeholder="领域(可选)" />
        <span v-if="busy" class="tip">抽取中,可能要几十秒…</span>
        <span v-else-if="p.ingest === 'ok'" class="tip ok">{{ p.ingestMsg }}</span>
        <span v-else-if="p.ingest === 'error'" class="tip bad">{{ p.ingestErr }}</span>
        <span v-else class="tip dim">Ctrl + Enter 收录</span>
        <button class="go" :disabled="!draft.trim() || busy" @click="submit">
          {{ busy ? '收录中…' : '收录' }}
        </button>
      </div>
    </div>

    <div class="pn-kpi">
      <div><b>{{ fmtNum(p.stats.total_kus ?? 0) }}</b><span>知识单元</span></div>
      <div><b>{{ fmtNum(p.stats.total_relations ?? 0) }}</b><span>关系</span></div>
      <div><b>{{ fmtNum(p.stats.graph_nodes ?? 0) }}</b><span>图谱节点</span></div>
      <div><b>{{ fmtNum(p.stats.open_conflicts ?? 0) }}</b><span>待裁决冲突</span></div>
      <div><b>{{ fmtNum(p.stats.vector_size ?? 0) }}</b><span>向量条目</span></div>
    </div>

    <div class="pn-sec">
      <div class="pn-h">最近录入 <em>{{ p.recentTotal }}</em></div>
      <div v-if="!p.recent.length" class="pn-empty">
        还没有录入知识。上面写一句话就能收录 —— 长文档走知识星球。
      </div>
      <div
        v-for="k in p.recent"
        :key="k.ku_id"
        class="pn-row clickable"
        :title="k.summary || k.name"
        @click="openKu(k.ku_id)"
      >
        <span class="m">
          <b>{{ clip(k.name || k.ku_id, 46) }}</b>
          <span class="s">{{ k.ku_type }} · {{ k.domain || '未分域' }}</span>
        </span>
        <span class="r">{{ fmtTime(k.created_at) }}</span>
      </div>
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

        <div class="pn-h sec-gap">书籍来源 <em>{{ p.sources.length }}</em></div>
        <div v-if="!p.sources.length" class="pn-empty">
          还没有整本摄入的资料。PDF / EPUB 走知识星球导入。
        </div>
        <div v-for="(s, i) in p.sources.slice(0, 8)" :key="i" class="pn-row" :title="s.name || s.source">
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
      录入走 <code>POST /api/knowledge/ingest</code>(KDE 抽取 → KEE 演化),
      列表读 <code>/api/knowledge/recent</code>;书籍走知识星球。
    </div>
  </PanelBox>
</template>

<style scoped>
/* ── 录入卡 ── */
.kb-in {
  margin-bottom: 14px;
  border-radius: var(--r-mid);
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(26, 157, 95, 0.16);
  overflow: hidden;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.kb-in:focus-within {
  border-color: rgba(26, 157, 95, 0.38);
  box-shadow: 0 2px 12px rgba(26, 157, 95, 0.08);
}
.kb-in textarea {
  display: block; width: 100%;
  padding: 11px 12px 6px;
  font-family: var(--sans);
  font-size: 13px; line-height: 1.7; color: var(--ink);
  background: transparent; border: 0; outline: none; resize: vertical;
  min-height: 62px;
}
.kb-in textarea::placeholder { color: var(--faint); }

.kb-bar {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 8px 8px 12px;
  border-top: 1px solid var(--line-soft);
}
.kb-bar input {
  padding: 4px 8px; border-radius: 6px;
  font-size: 11.5px; color: var(--ink);
  background: rgba(23, 32, 26, 0.04); border: 1px solid transparent;
  outline: none;
}
.kb-bar input:focus { border-color: var(--line); background: #fff; }
.kb-bar input::placeholder { color: var(--faint); }
.f-title { flex: 1 1 110px; max-width: 190px; min-width: 0; }
.f-domain { flex: 0 1 96px; min-width: 60px; }

.tip {
  flex: 1 1 auto; min-width: 0;
  font-size: 10.5px; color: var(--dim);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.tip.dim { color: var(--faint); }
.tip.ok { color: var(--green); }
.tip.bad { color: var(--red); }

.go {
  flex-shrink: 0;
  padding: 6px 15px; border-radius: var(--r-pill);
  font-size: 11.5px; font-weight: 600; color: #fff;
  background: var(--green);
  transition: opacity 0.15s, transform 0.12s;
}
.go:hover:not(:disabled) { opacity: 0.9; }
.go:active:not(:disabled) { transform: scale(0.97); }
.go:disabled { background: rgba(23, 32, 26, 0.12); color: var(--faint); }

.meta { margin-top: 8px; }
.sec-gap { margin-top: 14px; }
</style>
