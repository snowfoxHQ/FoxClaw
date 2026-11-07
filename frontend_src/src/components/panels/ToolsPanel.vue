<script setup>
import { computed, onMounted, ref } from 'vue'
import PanelBox from './PanelBox.vue'
import { panels, loadPanel, refreshPanel, openMdSkill, toggleMdSkill } from '../../stores/panels.js'
import { getJSON } from '../../api.js'
import { clip } from '../../lib/fmt.js'

const p = panels.tools
const busy = ref('')
const err = ref('')
/* 连接器 = MCP 服务。左栏入口叫「技能·连接器」,这一节就得在面板里真的存在,
   否则用户点进来找不到「外部应用」。目前为空,是因为还没有注册任何 MCP 服务。 */
const mcp = ref([])
onMounted(async () => {
  loadPanel('tools')
  try {
    const d = await getJSON('/ahs/mcp/servers')
    mcp.value = d.servers || []
  } catch {
    mcp.value = []
  }
})

const ready = computed(() => p.skills.length > 0 || p.md.length > 0)
const blocked = computed(() =>
  p.skills.filter((s) => s.blocked).length + p.md.filter((s) => s.blocked).length
)
const breakerN = computed(() => Object.keys(p.breakers || {}).length)

const TRUST = { high: 'green', medium: 'amber', low: 'red' }
const trustCN = (t) => ({ high: '高信任', medium: '中信任', low: '低信任' }[t] || t || '—')

/* MCP 服务的信任档位用的是 high/mid/low,和技能那套 high/medium/low 不是一套词 */
const MCP_TRUST = { high: 'green', mid: 'amber', low: 'red' }
const mcpTrustCN = (t) => ({ high: '高信任', mid: '中信任', low: '低信任' }[t] || '中信任')

async function pickMd(s) {
  err.value = ''
  try { await openMdSkill(s.id) } catch (e) { err.value = String(e.message || e) }
}

async function flip(s) {
  busy.value = s.id
  err.value = ''
  try { await toggleMdSkill(s.id, !s.enabled) } catch (e) { err.value = String(e.message || e) }
  busy.value = ''
}
</script>

<template>
  <PanelBox
    title="技能"
    tag="Tools"
    :state="p.state"
    :ready="ready"
    @refresh="refreshPanel('tools')"
  >
    <div class="pn-kpi">
      <div><b>{{ p.skills.length + p.md.length }}</b><span>技能总数</span></div>
      <div><b>{{ p.skills.length }}</b><span>执行技能</span></div>
      <div><b>{{ p.md.length }}</b><span>指令技能</span></div>
      <div><b>{{ blocked }}</b><span>被封杀</span></div>
      <div><b>{{ breakerN }}</b><span>熔断中</span></div>
    </div>

    <div v-if="err" class="pn-st err">{{ err }}</div>

    <div class="pn-cols">
      <div class="pn-sec">
        <div class="pn-h">执行技能 <em>{{ p.skills.length }}</em></div>
        <div v-if="!p.skills.length" class="pn-empty">没有注册的执行技能。</div>
        <div v-for="s in p.skills" :key="s.id" class="pn-row" :title="s.description">
          <span class="m">
            <b>{{ s.name || s.id }}</b>
            <span class="s">{{ s.kind }} · {{ s.side_effect || 'read' }}<template v-if="s.blocked"> · 已封杀</template></span>
          </span>
          <span class="pn-tag" :class="TRUST[s.trust] || ''">{{ trustCN(s.trust) }}</span>
        </div>
      </div>

      <div class="pn-sec">
        <div class="pn-h">指令技能 <em>{{ p.md.length }}</em></div>
        <div v-if="!p.md.length" class="pn-empty">没有 SKILL.md 指令技能。</div>
        <div v-for="s in p.md" :key="s.id" class="pn-row clickable" :title="'点击查看 SKILL.md 正文'" @click="pickMd(s)">
          <span class="m">
            <b>{{ s.name || s.id }}</b>
            <span class="s">{{ clip(s.description || '无描述', 34) }}</span>
          </span>
          <span class="pn-tag" :class="TRUST[s.trust] || ''">{{ trustCN(s.trust) }}</span>
          <button
            class="sw"
            :class="{ on: s.enabled }"
            :disabled="busy === s.id"
            :title="s.enabled ? '点击停用' : '点击启用'"
            @click.stop="flip(s)"
          >
            {{ s.enabled ? '启用' : '停用' }}
          </button>
        </div>
      </div>
    </div>

    <div class="pn-sec">
      <div class="pn-h">连接器 <em>{{ mcp.length }}</em></div>
      <div v-if="!mcp.length" class="pn-empty">
        还没接入外部应用。邮箱、你自己的另一个知识库这类需要授权的,会作为 MCP 服务出现在这一节。
      </div>
      <div v-for="s in mcp" :key="s.name" class="pn-row">
        <span class="m">
          <b>{{ s.name }}</b>
          <span class="s">{{ (s.tools || []).length }} 个工具<template v-if="s.transport"> · {{ s.transport }}</template></span>
        </span>
        <span class="pn-tag" :class="MCP_TRUST[s.trust || 'mid']">{{ mcpTrustCN(s.trust) }}</span>
      </div>
    </div>

    <div class="pn-note">
      指令技能点开即读 <code>/api/ahs/md-skills/{id}</code> 的正文,在中间舞台按文字版打开;
      开关走 <code>PATCH /api/ahs/md-skills/{id}</code>,与后端注册表同步。
      连接器走 <code>/api/ahs/mcp/servers</code>,与输入框 ⚙ 里那份是同一份数据。
    </div>
  </PanelBox>
</template>

<style scoped>
.sw {
  flex-shrink: 0; padding: 3px 9px; border-radius: var(--r-pill);
  font-size: 10px; font-family: var(--mono);
  background: var(--surface-2); color: var(--faint);
  border: 1px solid transparent;
}
.sw.on { background: rgba(26, 157, 95, 0.12); color: var(--green); }
.sw:disabled { opacity: 0.5; }
</style>
