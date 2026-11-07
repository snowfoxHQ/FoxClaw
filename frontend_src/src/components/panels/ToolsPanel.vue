<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import PanelBox from './PanelBox.vue'
import { panels, loadPanel, refreshPanel, openMdSkill, toggleMdSkill } from '../../stores/panels.js'
import { getJSON, postJSON, delJSON } from '../../api.js'
import { clip } from '../../lib/fmt.js'

const p = panels.tools
const busy = ref('')
const err = ref('')

const mcp = ref([])
async function loadMcp() {
  try {
    const d = await getJSON('/ahs/mcp/servers')
    mcp.value = d.servers || []
  } catch {
    mcp.value = []
  }
}
onMounted(() => {
  loadPanel('tools')
  loadMcp()
})

const ready = computed(() => p.skills.length > 0 || p.md.length > 0 || mcp.value.length > 0)
const blocked = computed(() =>
  p.skills.filter((s) => s.blocked).length + p.md.filter((s) => s.blocked).length
)
const breakerN = computed(() => Object.keys(p.breakers || {}).length)

const TRUST = { high: 'green', medium: 'amber', low: 'red' }
const trustCN = (t) => ({ high: '高信任', medium: '中信任', low: '低信任' }[t] || t || '—')

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

/* ── 单技能测试:POST /ahs/skills/{id}/test ──
   走真管线(权限/断路器/SSRF),所以界面要能填参数,也要把 needs_confirmation /
   blocked / circuit_open 这些非成功态原样显示,不能只画个绿勾。 */
const tId = ref('')
const tArgs = ref({})
const tBusy = ref(false)
const tRes = ref(null)
const tErr = ref('')
const tNonce = ref('')

function endTest() {
  tId.value = ''
  tArgs.value = {}
  tRes.value = null
  tErr.value = ''
  tNonce.value = ''
}

function beginTest(s) {
  if (tId.value === s.id) { endTest(); return }
  const a = {}
  for (const [k] of Object.entries(s.params || {})) a[k] = ''
  tId.value = s.id
  tArgs.value = a
  tRes.value = null
  tErr.value = ''
  tNonce.value = ''
  err.value = ''
}

/* params 是 {名: 类型} 的朴素 schema,类型只是提示;这里按声明做最小转换,其余原样送字符串 */
function coerce(params, args) {
  const out = {}
  for (const [k, ty] of Object.entries(params || {})) {
    const raw = String(args[k] ?? '').trim()
    if (raw === '') continue
    const t = String(ty || '').toLowerCase()
    if (t.includes('bool')) out[k] = /^(1|true|yes|y|是)$/i.test(raw)
    else if (t.includes('float') || t.includes('number')) out[k] = Number(raw)
    else if (t.includes('int')) out[k] = parseInt(raw, 10) || 0
    else out[k] = raw
  }
  return out
}

async function runTest(s, confirm = false) {
  if (tBusy.value) return
  tBusy.value = true
  tErr.value = ''
  try {
    const d = await postJSON(`/ahs/skills/${encodeURIComponent(s.id)}/test`, {
      args: coerce(s.params, tArgs.value),
      confirmed: confirm,
      mode: 'strict',
      confirm_nonce: confirm ? tNonce.value : '',
    })
    tRes.value = d
    tNonce.value = d.confirm_nonce || ''
  } catch (e) {
    tErr.value = String(e.message || e)
  } finally {
    tBusy.value = false
  }
}

const pretty = (o) => {
  if (o == null) return ''
  if (typeof o === 'string') return o
  try { return JSON.stringify(o, null, 2) } catch { return String(o) }
}

async function dropSkill(s) {
  if (!confirm(`删除技能「${s.name || s.id}」?它的触发词和参数会一起消失。`)) return
  busy.value = s.id
  err.value = ''
  try {
    await delJSON(`/ahs/skills/${encodeURIComponent(s.id)}`)
    if (tId.value === s.id) endTest()
    await refreshPanel('tools')
    await loadMcp()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = ''
  }
}

const splitList = (t) => String(t || '').split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean)

/* "city:str, days:int" → {city:"str", days:"int"} */
function parseParams(txt) {
  const out = {}
  for (const part of String(txt || '').split(/[,，;\n]/)) {
    const i = part.search(/[:：]/)
    const k = (i < 0 ? part : part.slice(0, i)).trim()
    const v = i < 0 ? '' : part.slice(i + 1).trim()
    if (k) out[k] = v || 'str'
  }
  return out
}

/* "Authorization: Bearer x" 每行一条 → dict(空行 / 无冒号行忽略) */
function parseHeaders(txt) {
  const out = {}
  for (const line of String(txt || '').split('\n')) {
    const i = line.indexOf(':')
    if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim()
  }
  return out
}

/* ── 安装技能:POST /ahs/skills ──
   后端只收 web_api / mcp;审核门 deny → 403, warn → 409 needs_ack(要再点一次「已知风险」) */
const SIDE = { read: 'read · 只读', write: 'write · 写入', send: 'send · 代发', pay: 'pay · 付款' }
const showIns = ref(false)
const insBusy = ref(false)
const insErr = ref('')
const insOk = ref('')
const insAck = ref(false)
const insParams = ref('query:str')
const insHeaders = ref('')
const ins = reactive({
  id: '', name: '', description: '', kind: 'web_api', side_effect: 'read',
  endpoint: '', http_method: 'GET', mcp_server: '', mcp_tool: '', triggers: '',
  timeout: 10, destructive: false, external: true, allow_local: false,
})

function toggleIns() {
  showIns.value = !showIns.value
  insErr.value = ''
  insOk.value = ''
  insAck.value = false
}

async function doInstall(ack = false) {
  if (insBusy.value) return
  const id = ins.id.trim()
  insErr.value = ''
  insOk.value = ''
  if (!id) { insErr.value = '技能 id 必填'; return }
  insBusy.value = true
  const body = {
    id,
    name: (ins.name || id).trim(),
    description: ins.description.trim(),
    kind: ins.kind,
    triggers: splitList(ins.triggers),
    side_effect: ins.side_effect,
    destructive: !!ins.destructive,
    external: !!ins.external,
    params: parseParams(insParams.value),
    endpoint: ins.kind === 'web_api' ? ins.endpoint.trim() : '',
    http_method: ins.http_method,
    http_headers: parseHeaders(insHeaders.value),
    param_location: 'auto',
    allow_local: !!ins.allow_local,
    timeout: Number(ins.timeout) || 10,
    mcp_server: ins.kind === 'mcp' ? ins.mcp_server.trim() : '',
    mcp_tool: ins.kind === 'mcp' ? ins.mcp_tool.trim() : '',
    acknowledged: ack,
  }
  try {
    const d = await postJSON('/ahs/skills', body)
    const warn = (d.warnings || []).length ? ` · ${d.warnings.join('；')}` : ''
    insOk.value = `${d.created ? '已安装' : '已更新'}「${body.name}」${warn}`
    insAck.value = false
    Object.assign(ins, { id: '', name: '', description: '', endpoint: '', triggers: '', mcp_server: '', mcp_tool: '' })
    await refreshPanel('tools')
  } catch (e) {
    insErr.value = String(e.message || e)
    insAck.value = !!(e.detail && e.detail.needs_ack)
  } finally {
    insBusy.value = false
  }
}

/* ── 连接器:POST|DELETE /ahs/mcp/servers ──
   接入会真连一次 server 拉工具清单(可能几十秒);连不上是 502,原样显示 */
const showMcpAdd = ref(false)
const mcpBusy = ref(false)
const mcpErr = ref('')
const mcpOk = ref('')
const mcpAck = ref(false)
const mf = reactive({ id: '', name: '', url: '', transport: 'streamable_http', headers: '' })

function toggleMcpAdd() {
  showMcpAdd.value = !showMcpAdd.value
  mcpErr.value = ''
  mcpOk.value = ''
  mcpAck.value = false
}

async function addMcp(ack = false) {
  if (mcpBusy.value) return
  const id = mf.id.trim()
  mcpErr.value = ''
  mcpOk.value = ''
  if (!id || !mf.url.trim()) { mcpErr.value = 'id 和 URL 都要填'; return }
  mcpBusy.value = true
  try {
    const d = await postJSON('/ahs/mcp/servers', {
      id,
      name: mf.name.trim() || id,
      url: mf.url.trim(),
      headers: parseHeaders(mf.headers),
      transport: mf.transport,
      acknowledged: ack,
    })
    mcpOk.value = `已接入 ${d.server?.name || id} · 注册 ${d.tools_registered ?? 0} 个工具`
    mcpAck.value = false
    Object.assign(mf, { id: '', name: '', url: '', headers: '' })
    await loadMcp()
    await refreshPanel('tools')
  } catch (e) {
    mcpErr.value = String(e.message || e)
    mcpAck.value = !!(e.detail && e.detail.needs_ack)
  } finally {
    mcpBusy.value = false
  }
}

/* 重连:server 那边的工具会变,POST .../refresh 增量对齐注册表 */
async function refreshMcp(s) {
  if (mcpBusy.value) return
  mcpBusy.value = true
  mcpErr.value = ''
  mcpOk.value = ''
  try {
    const d = await postJSON(`/ahs/mcp/servers/${encodeURIComponent(s.id)}/refresh`, {})
    mcpOk.value = `已重连 ${s.name || s.id} · 现共 ${d.tools ?? 0} 个工具`
    await loadMcp()
    await refreshPanel('tools')
  } catch (e) {
    mcpErr.value = String(e.message || e)
  } finally {
    mcpBusy.value = false
  }
}

async function delMcp(s) {
  if (!confirm(`移除连接器「${s.name || s.id}」?它注册的 MCP 技能会一起删掉。`)) return
  err.value = ''
  try {
    const d = await delJSON(`/ahs/mcp/servers/${encodeURIComponent(s.id)}`)
    await loadMcp()
    await refreshPanel('tools')
    mcpOk.value = `已移除,连带删除 ${d.removed_skills ?? 0} 个技能`
  } catch (e) {
    err.value = String(e.message || e)
  }
}
</script>

<template>
  <PanelBox
    title="技能"
    tag="Tools"
    :state="p.state"
    :ready="ready"
    @refresh="refreshPanel('tools'); loadMcp()"
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
        <template v-for="s in p.skills" :key="s.id">
          <div class="pn-row" :title="s.description">
            <span class="m">
              <b>{{ s.name || s.id }}</b>
              <span class="s">
                {{ s.kind }} · {{ s.side_effect || 'read' }}<template v-if="s.source === 'user'"> · 用户安装</template><template v-if="s.blocked"> · 已封杀</template>
              </span>
            </span>
            <span class="pn-tag" :class="TRUST[s.trust] || ''">{{ trustCN(s.trust) }}</span>
            <button class="mbtn" :class="{ on: tId === s.id }" @click="beginTest(s)">
              {{ tId === s.id ? '收起' : '测试' }}
            </button>
            <button v-if="s.source !== 'builtin'" class="mbtn danger" :disabled="busy === s.id" @click="dropSkill(s)">
              删除
            </button>
          </div>

          <div v-if="tId === s.id" class="tk-test">
            <div v-if="!(s.params && Object.keys(s.params).length)" class="tip dim">
              该技能没有声明参数,直接运行即可。
            </div>
            <label v-for="(ty, k) in (s.params || {})" :key="k" class="f">
              <span>{{ k }}<em>{{ ty }}</em></span>
              <input v-model="tArgs[k]" :placeholder="String(ty || 'str')" spellcheck="false" />
            </label>
            <div class="fbar">
              <button class="go" :disabled="tBusy" @click="runTest(s, false)">
                {{ tBusy ? '调用中…' : '运行' }}
              </button>
              <button
                v-if="tRes && tRes.status === 'needs_confirmation'"
                class="go warn"
                :disabled="tBusy"
                @click="runTest(s, true)"
              >
                确认并重跑
              </button>
              <span class="tip dim">走真实权限 / 断路器 / SSRF 校验</span>
            </div>
            <div v-if="tErr" class="pn-note meta bad">{{ tErr }}</div>
            <div v-if="tRes" class="tk-res" :class="tRes.success ? 'ok' : 'bad'">
              <div class="r-h">
                <b>{{ tRes.status }}</b>
                <span v-if="tRes.message">{{ tRes.message }}</span>
              </div>
              <pre v-if="pretty(tRes.output)">{{ pretty(tRes.output) }}</pre>
            </div>
          </div>
        </template>
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
      <div class="pn-h">
        安装技能
        <button class="mbtn" @click="toggleIns">{{ showIns ? '收起' : '+ 新增' }}</button>
      </div>
      <div v-if="showIns" class="tk-form">
        <div class="frow">
          <label class="f"><span>id</span>
            <input v-model="ins.id" placeholder="weather_query" spellcheck="false" />
          </label>
          <label class="f"><span>名称</span>
            <input v-model="ins.name" placeholder="查天气" />
          </label>
        </div>
        <label class="f"><span>描述</span>
          <input v-model="ins.description" placeholder="给模型看的说明:能做什么,什么时候用" />
        </label>
        <div class="frow">
          <label class="f"><span>类型</span>
            <select v-model="ins.kind">
              <option value="web_api">web_api · HTTP 接口</option>
              <option value="mcp">mcp · 已有 server 的工具</option>
            </select>
          </label>
          <label class="f"><span>副作用</span>
            <select v-model="ins.side_effect">
              <option v-for="(cn, k) in SIDE" :key="k" :value="k">{{ cn }}</option>
            </select>
          </label>
          <label class="f narrow"><span>超时(s)</span>
            <input v-model.number="ins.timeout" type="number" min="1" max="120" />
          </label>
        </div>
        <label class="f"><span>触发词(逗号分隔)</span>
          <input v-model="ins.triggers" placeholder="天气, 气温, 下雨" />
        </label>
        <label class="f"><span>参数(schema:名:类型)</span>
          <input v-model="insParams" placeholder="city:str, days:int" spellcheck="false" />
        </label>

        <template v-if="ins.kind === 'web_api'">
          <div class="frow">
            <label class="f grow"><span>endpoint</span>
              <input v-model="ins.endpoint" placeholder="https://api.example.com/weather" spellcheck="false" />
            </label>
            <label class="f narrow"><span>方法</span>
              <select v-model="ins.http_method">
                <option>GET</option>
                <option>POST</option>
              </select>
            </label>
          </div>
          <label class="f"><span>请求头(每行 Key: Value)</span>
            <textarea v-model="insHeaders" rows="2" spellcheck="false" placeholder="Authorization: Bearer xxx" />
          </label>
          <label class="chk"><input v-model="ins.allow_local" type="checkbox" /> 允许访问内网 / 本机地址</label>
        </template>
        <div v-else class="frow">
          <label class="f"><span>MCP server</span>
            <input v-model="ins.mcp_server" placeholder="amap-maps" spellcheck="false" />
          </label>
          <label class="f"><span>工具名</span>
            <input v-model="ins.mcp_tool" placeholder="maps_direction" spellcheck="false" />
          </label>
        </div>

        <label class="chk"><input v-model="ins.destructive" type="checkbox" /> 不可逆操作(删除 / 付款这类)</label>
        <label class="chk"><input v-model="ins.external" type="checkbox" /> 会调用外部服务(联网)</label>

        <div class="fbar">
          <button class="go" :disabled="insBusy" @click="doInstall(false)">
            {{ insBusy ? '安装中…' : '安装' }}
          </button>
          <button v-if="insAck" class="go warn" :disabled="insBusy" @click="doInstall(true)">
            已知风险,继续安装
          </button>
          <span v-if="insErr" class="tip bad">{{ insErr }}</span>
          <span v-else-if="insOk" class="tip ok">{{ insOk }}</span>
          <span v-else class="tip dim">同 id 覆盖更新;内置技能的 id 不可覆盖</span>
        </div>
      </div>
      <div v-else class="pn-note">
        只能装 <code>web_api</code> 与 <code>mcp</code> 两类 —— 本地代码技能不接受从界面安装。
      </div>
    </div>

    <div class="pn-sec">
      <div class="pn-h">
        连接器 <em>{{ mcp.length }}</em>
        <button class="mbtn" @click="toggleMcpAdd">{{ showMcpAdd ? '收起' : '+ 接入' }}</button>
      </div>

      <div v-if="showMcpAdd" class="tk-form">
        <div class="frow">
          <label class="f"><span>id</span>
            <input v-model="mf.id" placeholder="my-notes" spellcheck="false" />
          </label>
          <label class="f"><span>显示名</span>
            <input v-model="mf.name" placeholder="我的备忘录" />
          </label>
        </div>
        <label class="f"><span>URL</span>
          <input v-model="mf.url" placeholder="https://example.com/mcp" spellcheck="false" />
        </label>
        <div class="frow">
          <label class="f narrow"><span>传输</span>
            <select v-model="mf.transport">
              <option value="streamable_http">streamable_http</option>
              <option value="sse">sse</option>
            </select>
          </label>
          <label class="f grow"><span>请求头(每行 Key: Value)</span>
            <textarea v-model="mf.headers" rows="2" spellcheck="false" placeholder="Authorization: Bearer xxx" />
          </label>
        </div>
        <div class="fbar">
          <button class="go" :disabled="mcpBusy" @click="addMcp(false)">
            {{ mcpBusy ? '连接中…' : '接入' }}
          </button>
          <button v-if="mcpAck" class="go warn" :disabled="mcpBusy" @click="addMcp(true)">
            已知风险,继续
          </button>
          <span v-if="mcpErr" class="tip bad">{{ mcpErr }}</span>
          <span v-else-if="mcpOk" class="tip ok">{{ mcpOk }}</span>
          <span v-else class="tip dim">接入后会连一次,把它暴露的工具注册成技能</span>
        </div>
      </div>

      <div v-if="!mcp.length" class="pn-empty">
        还没接入外部应用。邮箱、你自己的另一个知识库这类需要授权的,会作为 MCP 服务出现在这一节。
      </div>
      <div v-for="s in mcp" :key="s.id" class="pn-row" :title="s.url">
        <span class="m">
          <b>{{ s.name || s.id }}</b>
          <span class="s">{{ s.tool_count ?? 0 }} 个工具<template v-if="s.transport"> · {{ s.transport }}</template></span>
        </span>
        <button class="mbtn" :disabled="mcpBusy" @click="refreshMcp(s)">重连</button>
        <button class="mbtn danger" @click="delMcp(s)">移除</button>
      </div>
    </div>

    <div class="pn-note">
      指令技能正文 <code>/api/ahs/md-skills/{id}</code>,执行技能运行 <code>POST /api/ahs/skills/{id}/test</code>;
      连接器与技能面板是同一份数据(<code>/api/ahs/mcp/servers</code>)。
    </div>
  </PanelBox>
</template>

<style scoped>
.sw {
  flex-shrink: 0; padding: 4px 10px; border-radius: var(--r-pill);
  font-size: 11px; font-family: var(--mono);
  background: var(--surface-2); color: var(--faint);
  border: 1px solid transparent;
}
.sw.on { background: rgba(26, 157, 95, 0.12); color: var(--green); }
.sw:disabled { opacity: 0.5; }

/* 行内小按钮:测试 / 删除 / 新增 —— 原来「执行技能」整行是死的,点了没反应 */
.mbtn {
  flex-shrink: 0; padding: 4px 10px; border-radius: var(--r-pill);
  font-size: 11px; font-family: var(--mono); color: var(--dim);
  background: rgba(255, 255, 255, 0.8); border: 1px solid var(--line-soft);
  transition: color 0.13s, border-color 0.13s, background 0.13s;
}
.mbtn:hover { color: var(--green); border-color: rgba(26, 157, 95, 0.35); background: #fff; }
.mbtn.on { color: var(--green); background: rgba(26, 157, 95, 0.1); border-color: rgba(26, 157, 95, 0.4); }
.mbtn.danger:hover { color: var(--red); border-color: rgba(229, 72, 77, 0.35); }
.mbtn:disabled { opacity: 0.5; }

/* ── 表单(安装技能 / 接入连接器 / 技能测试)── */
.tk-form, .tk-test {
  margin: 4px 0 6px;
  padding: 11px 12px;
  border-radius: var(--r-mid);
  background: rgba(255, 255, 255, 0.86);
  border: 1px solid var(--line-soft);
}
.tk-test { display: flex; flex-direction: column; gap: 7px; }
.tk-test .f span em {
  font-style: normal; font-weight: 600; font-family: var(--mono); font-size: 11px; color: var(--dim);
}

.frow { display: flex; gap: 8px; }
.frow .f { flex: 1 1 0; min-width: 0; }
.frow .f.grow { flex: 2 1 0; }
.frow .f.narrow { flex: 0 1 104px; }
.f { display: block; margin-bottom: 7px; }
.f > span {
  display: block; margin-bottom: 3px;
  font-size: 11px; color: var(--faint);
}
.f input, .f select, .f textarea {
  display: block; width: 100%;
  padding: 5px 8px; border-radius: 6px;
  font-family: inherit; font-size: 12.5px; color: var(--ink);
  background: rgba(23, 32, 26, 0.04);
  border: 1px solid transparent; outline: none;
  transition: border-color 0.14s, background 0.14s;
}
.f textarea { resize: vertical; line-height: 1.6; }
.f input:focus, .f select:focus, .f textarea:focus { border-color: var(--line); background: #fff; }
.f input::placeholder, .f textarea::placeholder { color: var(--dim); }

.chk {
  display: flex; align-items: center; gap: 6px;
  margin-bottom: 6px; font-size: 12px; color: var(--dim);
}
.chk input { accent-color: var(--green); }

.fbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 3px; }
.go {
  flex-shrink: 0; padding: 6px 15px; border-radius: var(--r-pill);
  font-size: 12.5px; font-weight: 600; color: #fff; background: var(--green);
  transition: opacity 0.15s, transform 0.12s;
}
.go:hover:not(:disabled) { opacity: 0.9; }
.go:active:not(:disabled) { transform: scale(0.97); }
.go:disabled { background: rgba(23, 32, 26, 0.12); color: var(--faint); }
.go.warn { background: var(--amber); }

.tip {
  flex: 1 1 auto; min-width: 0;
  font-size: 11.5px; color: var(--dim);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.tip.dim { color: var(--faint); }
.tip.ok { color: var(--green); }
.tip.bad { color: var(--red); white-space: normal; }

/* 测试结果:成功/失败都原文展示,失败(needs_confirmation/blocked)不涂成成功色 */
.tk-res {
  margin-top: 4px; padding: 8px 10px; border-radius: var(--r-sm);
  background: rgba(23, 32, 26, 0.03); border: 1px solid var(--line-soft);
}
.tk-res .r-h { display: flex; gap: 6px; font-size: 12px; color: var(--dim); }
.tk-res .r-h b { font-family: var(--mono); font-size: 11.5px; color: var(--ink); }
.tk-res.bad .r-h b { color: var(--amber); }
.tk-res pre {
  margin: 6px 0 0; max-height: 220px; overflow: auto;
  font-family: var(--mono); font-size: 11.5px; line-height: 1.6; color: var(--dim);
  white-space: pre-wrap; word-break: break-word;
}
.pn-note.meta { margin-top: 6px; }
.pn-note.meta.bad { color: var(--red); }
</style>
