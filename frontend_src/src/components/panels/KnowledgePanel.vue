<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import PanelBox from './PanelBox.vue'
import { panels, loadPanel, refreshPanel, ingestKnowledge, openKu } from '../../stores/panels.js'
import { getJSON, postJSON } from '../../api.js'
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

/* ── 检索:POST /api/knowledge/search ──
   后端一直有这条混合检索,界面却只给「最近录入」,想按关键词翻库只能去知识星球。 */
const q = ref('')
const mode = ref('hybrid')
const MODES = [
  { v: 'hybrid', label: '混合' },
  { v: 'bm25', label: '关键词' },
  { v: 'vector', label: '语义' },
  { v: 'graph', label: '图谱' },
]
const sState = ref('idle') // idle | busy | ok | err
const sErr = ref('')
const hits = ref([])

async function doSearch() {
  const text = q.value.trim()
  if (!text || sState.value === 'busy') return
  sState.value = 'busy'
  sErr.value = ''
  try {
    const d = await postJSON('/knowledge/search', {
      query: text,
      mode: mode.value,
      top_k: 12,
    })
    hits.value = d.hits || []
    if (d.available === false) {
      sState.value = 'err'
      sErr.value = 'HKC 未启用(未 vendor 或缺依赖)'
    } else if (d.error) {
      sState.value = 'err'
      sErr.value = d.error
    } else {
      sState.value = 'ok'
    }
  } catch (e) {
    hits.value = []
    sState.value = 'err'
    sErr.value = e.message
  }
}

/* ── 能力详情:GET /api/knowledge/ability/{key} ──
   能力 chip 以前是死的,点了没反应 —— 而这条接口正是"这个能力到底够不够"的答案。 */
const abKey = ref('')
const abState = ref('idle') // idle | busy | ok | err
const abErr = ref('')
const ab = ref(null)

async function showAbility(k) {
  if (abKey.value === k && ab.value) {
    abKey.value = ''
    ab.value = null
    abState.value = 'idle'
    return
  }
  abKey.value = k
  abState.value = 'busy'
  abErr.value = ''
  ab.value = null
  try {
    ab.value = await getJSON(`/knowledge/ability/${encodeURIComponent(k)}`)
    abState.value = 'ok'
  } catch (e) {
    abState.value = 'err'
    abErr.value = e.message
  }
}

/* coverage 在不同能力下是数字或对象,取几个常见键;取不到就显示 '—',不猜格式 */
function covText(c) {
  if (c == null) return ''
  if (typeof c === 'number') return `${Math.round(c * 100)}%`
  if (typeof c === 'object') {
    const n = c.overall ?? c.score ?? c.value ?? c.ratio
    if (typeof n === 'number') return `${Math.round(n * 100)}%`
  }
  return ''
}
</script>

<template>
  <PanelBox
    title="学习库"
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
      <div class="pn-h">检索 <em>{{ hits.length }}</em></div>
      <div class="kb-find">
        <input
          v-model="q"
          placeholder="按关键词 / 语义检索整个知识库…"
          @keydown.enter="doSearch"
        />
        <select v-model="mode" aria-label="检索模式">
          <option v-for="m in MODES" :key="m.v" :value="m.v">{{ m.label }}</option>
        </select>
        <button class="go" :disabled="!q.trim() || sState === 'busy'" @click="doSearch">
          {{ sState === 'busy' ? '检索中…' : '检索' }}
        </button>
      </div>
      <div v-if="sState === 'err'" class="pn-note meta bad">{{ sErr }}</div>
      <div v-else-if="hits.length" class="kb-hits">
        <button
          v-for="h in hits"
          :key="h.ku_id"
          class="kb-hit"
          :title="h.summary || h.name"
          @click="openKu(h.ku_id)"
        >
          <span class="m">
            <b>{{ clip(h.name || h.ku_id, 52) }}</b>
            <span class="s">{{ h.ku_type || 'KU' }} · {{ h.mode || mode }}</span>
          </span>
          <span class="r">{{ h.score != null ? Number(h.score).toFixed(2) : '' }}</span>
        </button>
      </div>
      <div v-else-if="sState === 'ok'" class="pn-empty">没有命中,换个词或换模式试试。</div>
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
        <button
          v-for="a in p.abilities"
          :key="a"
          class="pn-chip"
          :class="{ on: abKey === a }"
          @click="showAbility(a)"
        >
          {{ a }}
        </button>
      </div>

      <div v-if="abState === 'busy'" class="pn-note meta">编译中…</div>
      <div v-else-if="abState === 'err'" class="pn-note meta bad">编译失败:{{ abErr }}</div>
      <div v-else-if="ab" class="kb-ab">
        <template v-if="ab.available === false">
          <div class="pn-note meta">HKC 未启用,无法编译能力。</div>
        </template>

        <!-- 覆盖不足不是错误,是"知识还不足以具备该能力"(证据驱动 gate) -->
        <template v-else-if="ab.sufficient === false">
          <div class="kb-ab-h">{{ abKey }}<span class="v">知识不足</span></div>
          <div class="pn-note meta">{{ ab.reason || '覆盖不足,暂不授予该能力。' }}</div>
          <div v-if="(ab.missing_skills || []).length" class="pn-chips gap">
            <span v-for="m in ab.missing_skills" :key="m" class="pn-chip warn">{{ m }}</span>
          </div>
        </template>

        <template v-else-if="ab.ability">
          <div class="kb-ab-h">
            {{ ab.ability.display_name || abKey }}
            <span class="v">{{ ab.ability.version || '' }}</span>
          </div>
          <div class="pn-note meta">
            {{ ab.ability.domain || '未分域' }} · 覆盖 {{ covText(ab.ability.coverage) || '—' }}
          </div>
          <div v-for="s in ab.ability.skills || []" :key="s.skill_key" class="pn-row">
            <span class="m">
              <b>{{ s.display_name || s.skill_key }}</b>
              <span class="s">{{ s.concept_hits ?? 0 }} 个概念命中</span>
            </span>
            <span class="r">{{ covText(s.coverage) }}</span>
          </div>
          <div v-for="(w, i) in ab.ability.workflows || []" :key="'w' + i" class="pn-row">
            <span class="m"><b>{{ w }}</b></span>
          </div>
        </template>
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
.kb-in textarea::placeholder { color: var(--dim); }

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
.kb-bar input::placeholder { color: var(--dim); }
.f-title { flex: 1 1 110px; max-width: 190px; min-width: 0; }
.f-domain { flex: 0 1 96px; min-width: 60px; }

.tip {
  flex: 1 1 auto; min-width: 0;
  font-size: 11.5px; color: var(--dim);
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
/* 禁用态原来用 --faint 压在 12% 墨底上,对比度不到 2:1、几乎读不出字。
   改成实心浅底 + 深墨字:一眼看得出「现在点不了」,但字仍然清楚。 */
.go:disabled {
  background: rgba(23, 32, 26, 0.07); color: var(--dim);
  border: 1px solid var(--line-soft);
}

.meta { margin-top: 8px; }
.sec-gap { margin-top: 14px; }
.pn-note.meta.bad { color: var(--red); }

/* ── 检索条 ── */
.kb-find {
  display: flex; align-items: center; gap: 6px;
  margin-bottom: 8px;
}
.kb-find input {
  flex: 1 1 auto; min-width: 0;
  padding: 7px 10px; border-radius: var(--r-sm);
  font-size: 12.5px; color: var(--ink);
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid var(--line-soft);
  outline: none;
  transition: border-color 0.15s;
}
.kb-find input:focus { border-color: rgba(26, 157, 95, 0.45); }
.kb-find input::placeholder { color: var(--dim); }
.kb-find select {
  padding: 6px 8px; border-radius: var(--r-sm);
  font-size: 12px; font-weight: 600; color: var(--ink);
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid var(--line);
  outline: none;
}
.kb-find .go { padding: 6px 13px; }

.kb-hits { display: flex; flex-direction: column; gap: 2px; }
.kb-hit {
  display: flex; align-items: center; gap: 8px; width: 100%;
  padding: 7px 9px; border-radius: var(--r-sm);
  text-align: left;
  transition: background 0.13s;
}
.kb-hit:hover { background: rgba(255, 255, 255, 0.75); }
.kb-hit .m { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.kb-hit .m b {
  font-size: 13px; font-weight: 600; color: var(--ink);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.kb-hit .m .s { font-size: 11.5px; color: var(--dim); }
.kb-hit .r {
  flex-shrink: 0; font-size: 11.5px; color: var(--dim);
  font-variant-numeric: tabular-nums;
}

/* ── 能力详情 ── */
.pn-chips.gap { margin-top: 8px; }
.kb-ab {
  margin-top: 10px; padding: 10px 12px;
  border-radius: var(--r-mid);
  background: rgba(255, 255, 255, 0.86);
  border: 1px solid var(--line-soft);
}
.kb-ab-h {
  display: flex; align-items: baseline; gap: 6px;
  font-size: 13px; font-weight: 600; color: var(--ink);
  margin-bottom: 5px;
}
.kb-ab-h .v { font-size: 11.5px; font-weight: 600; color: var(--dim); }
</style>
