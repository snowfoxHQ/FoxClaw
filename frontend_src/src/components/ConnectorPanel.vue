<script setup>
import { onMounted, ref } from 'vue'
import { getJSON } from '../api.js'

defineEmits(['back'])

const state = ref('loading')
const servers = ref([])
const skills = ref([])
const mds = ref([])

const BADGE = { high: '高信任', mid: '中信任', low: '低信任' }

onMounted(async () => {
  try {
    const [a, b, c] = await Promise.all([
      getJSON('/ahs/mcp/servers').catch(() => ({})),
      getJSON('/ahs/skills').catch(() => ({})),
      getJSON('/ahs/md-skills').catch(() => ({})),
    ])
    servers.value = a.servers || []
    skills.value = b.skills || []
    mds.value = c.skills || []
    state.value = 'ok'
  } catch {
    state.value = 'error'
  }
})
</script>

<template>
  <div class="cp">
    <div class="cp-head">
      <span class="back" title="返回" @click="$emit('back')">‹</span>
      <b>连接器 / 插件</b>
      <span class="tot">{{ servers.length + skills.length + mds.length }}</span>
    </div>

    <div v-if="state === 'loading'" class="empty">加载中…</div>
    <div v-else-if="state === 'error'" class="empty">后端未连接</div>

    <template v-else>
      <div class="sec">MCP 服务 <em>{{ servers.length }}</em></div>
      <div v-if="!servers.length" class="empty sm">未接入</div>
      <div v-for="s in servers" :key="s.name" class="row">
        <b>{{ s.name }}</b>
        <span class="bd" :class="s.trust || 'mid'">{{ BADGE[s.trust || 'mid'] }}</span>
      </div>

      <div class="sec">技能 <em>{{ skills.length }}</em></div>
      <div v-if="!skills.length" class="empty sm">未登记</div>
      <div v-for="s in skills" :key="s.name" class="row">
        <b>{{ s.name }}</b>
        <span class="kind">{{ s.kind || 'local' }}</span>
      </div>

      <div class="sec">SKILL.md <em>{{ mds.length }}</em></div>
      <div v-if="!mds.length" class="empty sm">未发现</div>
      <div v-for="s in mds" :key="s.name" class="row">
        <b>{{ s.name }}</b>
        <span class="kind">{{ s.scope || 'md' }}</span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.cp { display: flex; flex-direction: column; }
.cp-head {
  display: flex; align-items: center; gap: 7px;
  padding: 3px 4px 7px; border-bottom: 1px solid var(--line-soft); margin-bottom: 4px;
}
.cp-head b { font-size: 12px; font-weight: 600; }
.cp-head .back { cursor: pointer; color: var(--faint); font-size: 15px; line-height: 1; padding: 0 3px; }
.cp-head .back:hover { color: var(--ink); }
.cp-head .tot {
  margin-left: auto; font-size: 10px; color: var(--dim);
  padding: 1px 6px; border-radius: var(--r-pill); background: var(--surface-2);
}
.sec {
  display: flex; justify-content: space-between;
  padding: 9px 5px 3px;
  font-size: 9.5px; font-weight: 700; letter-spacing: 0.07em;
  color: var(--faint); text-transform: uppercase;
}
.sec em { font-style: normal; font-family: var(--mono); }
.row {
  display: flex; align-items: center; gap: 6px;
  padding: 5px 6px; border-radius: 7px;
  font-size: 11.5px;
}
.row:hover { background: var(--surface-2); }
.row b { font-weight: 500; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kind { font-family: var(--mono); font-size: 9.5px; color: var(--faint); }
.bd { font-size: 9px; padding: 1px 5px; border-radius: var(--r-pill); }
.bd.high { color: var(--green); background: rgba(26, 157, 95, 0.12); }
.bd.mid { color: var(--amber); background: rgba(200, 140, 40, 0.12); }
.bd.low { color: var(--red); background: rgba(200, 60, 60, 0.1); }
.empty { padding: 14px 6px; font-size: 11.5px; color: var(--faint); text-align: center; }
.empty.sm { padding: 5px 6px; text-align: left; }
</style>
