<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { attachFile, attachImage, chat, ingestFile, ingestFolder, say, setMode, ui } from '../stores/chat.js'
import { setModule } from '../stores/app.js'

const open = ref(false)
const root = ref(null)
const inFile = ref(null)
const inFolder = ref(null)
const inImg = ref(null)
const inAttach = ref(null)

const rows = computed(() => [
  {
    ic: chat.mode === 'stream' ? '⚡' : '🔧',
    b: chat.mode === 'stream' ? '流式对话' : '编排任务',
    s: '点击切换模式',
    act: () => setMode(chat.mode === 'stream' ? 'orchestrate' : 'stream'),
  },
  { ic: '📎', b: '上传文件', s: '摄入知识库', act: () => { close(); inFile.value?.click() } },
  { ic: '📁', b: '导入文件夹', s: '逐个摄入', act: () => { close(); inFolder.value?.click() } },
  { ic: '🖼', b: '附加图片', s: '发给视觉模型', act: () => { close(); inImg.value?.click() } },
  { ic: '📄', b: '附件文件', s: '随问题发给 AI', act: () => { close(); inAttach.value?.click() } },
  { ic: '🔌', b: '技能 · 连接器', s: '到技能面板看全部', act: () => { close(); setModule('tools') } },
])

function toggle() {
  open.value = !open.value
}
function close() {
  open.value = false
}
watch(() => ui.ingestTick, () => inFile.value?.click())

function onDocDown(e) {
  if (root.value && !root.value.contains(e.target)) close()
}
onMounted(() => document.addEventListener('mousedown', onDocDown, true))
onBeforeUnmount(() => document.removeEventListener('mousedown', onDocDown, true))

async function onIngest(e) {
  const f = e.target.files && e.target.files[0]
  e.target.value = ''
  if (f) await ingestFile(f)
}
async function onFolder(e) {
  const fs = [...(e.target.files || [])]
  e.target.value = ''
  if (fs.length) await ingestFolder(fs)
}
function onImage(e) {
  const fs = [...(e.target.files || [])]
  e.target.value = ''
  fs.forEach(attachImage)
  if (fs.length) say(`已附加 ${fs.length} 张图片`)
}
async function onAttach(e) {
  const fs = [...(e.target.files || [])]
  e.target.value = ''
  for (const f of fs) await attachFile(f)
}
</script>

<template>
  <div ref="root" class="tm">
    <button class="plus" :class="{ on: open }" title="工具:模式 / 上传 / 文件夹 / 图片 / 附件 / 连接器" @click="toggle">
      ＋
    </button>

    <div v-if="open" class="pop">
      <div class="sec">工具</div>
      <button v-for="(r, i) in rows" :key="i" class="it" @click="r.act()">
        <span class="ic">{{ r.ic }}</span>
        <span class="tx"><b>{{ r.b }}</b><span>{{ r.s }}</span></span>
      </button>
      <div class="foot">
        附件 {{ chat.attachments.length }} · 图片 {{ chat.images.length }} ·
        {{ chat.mode === 'stream' ? '流式' : '编排' }}
      </div>
    </div>

    <input ref="inFile" type="file" hidden accept=".pdf,.md,.txt,.epub,.mobi,.azw3,.docx,.xlsx" @change="onIngest" />
    <input ref="inFolder" type="file" hidden webkitdirectory directory multiple @change="onFolder" />
    <input ref="inImg" type="file" hidden accept="image/*" multiple @change="onImage" />
    <input ref="inAttach" type="file" hidden multiple @change="onAttach" />
  </div>
</template>

<style scoped>
.tm { position: relative; flex-shrink: 0; }

.plus {
  width: 28px; height: 28px; border-radius: 50%;
  display: grid; place-items: center;
  font-size: 15px; color: var(--dim);
  background: var(--surface-2);
  transition: color 0.15s, background 0.15s;
}
.plus:hover, .plus.on { color: var(--ink); background: var(--surface-3); }

.pop {
  position: absolute; left: 0; bottom: calc(100% + 8px);
  z-index: var(--z-menu);
  width: 250px; max-height: 54vh;
  display: flex; flex-direction: column;
  padding: 6px;
  border-radius: var(--r-mid);
  background: rgba(255, 255, 255, 0.92);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  backdrop-filter: blur(24px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.9);
  box-shadow: var(--sh-pop);
  overflow-y: auto;
  animation: tm-in 0.15s ease;
}
@keyframes tm-in { from { opacity: 0; transform: translateY(5px); } }

.sec {
  padding: 6px 7px 4px;
  font-size: 11px; font-weight: 700; letter-spacing: 0.08em;
  color: var(--dim); text-transform: uppercase;
}
.it {
  width: 100%;
  display: flex; align-items: center; gap: 9px;
  padding: 8px; border-radius: var(--r-sm);
  text-align: left;
  transition: background 0.13s;
}
.it:hover { background: var(--surface-2); }
.it .ic { font-size: 14px; width: 16px; text-align: center; flex-shrink: 0; }
.it .tx { display: flex; flex-direction: column; gap: 2px; }
.it .tx b { font-size: 13px; font-weight: 600; }
.it .tx span { font-size: 11px; color: var(--dim); }

.foot {
  margin-top: 4px; padding: 7px 7px 3px;
  border-top: 1px solid var(--line-soft);
  font-size: 11px; color: var(--dim); font-family: var(--mono);
}
</style>
