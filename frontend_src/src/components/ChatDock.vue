<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ComposerMenu from './ComposerMenu.vue'
import ModelPicker from './ModelPicker.vue'
import ToolMenu from './ToolMenu.vue'
import { getJSON } from '../api.js'
import {
  chat, dropAttachment, dropImage, newSession, pushLocal, requestIngest,
  runSearch, say, send, setMode, stop,
} from '../stores/chat.js'
import { setModule } from '../stores/app.js'
import { t } from '../lib/i18n.js'

const text = ref('')
const ta = ref(null)
const menu = ref(null) // 'slash' | 'at' | null
const mq = ref('')
const hover = ref(0)
const skills = ref([])

// 命令字面量保持中文(输入语法不变),只有说明文字跟随界面语言
const CMD = computed(() => [
  { key: '/新建', desc: t('chat.cmd.new'), run: () => newSession() },
  { key: '/流式', desc: t('chat.cmd.stream'), run: () => { setMode('stream'); say(t('chat.saidStream')) } },
  { key: '/任务', desc: t('chat.cmd.orchestrate'), run: () => { setMode('orchestrate'); say(t('chat.saidOrchestrate')) } },
  { key: '/搜索', desc: t('chat.cmd.search'), arg: true },
  { key: '/摄入', desc: t('chat.cmd.ingest'), run: () => requestIngest() },
  { key: '/设置', desc: t('chat.cmd.settings'), run: () => setModule('settings') },
  { key: '/停止', desc: t('chat.cmd.stop'), run: () => stop() },
  { key: '/清屏', desc: t('chat.cmd.clear'), run: () => { chat.messages = [] } },
  {
    key: '/帮助',
    desc: t('chat.cmd.help'),
    run: () => pushLocal(CMD.value.map((c) => `${c.key.padEnd(6)}  ${c.desc}`).join('\n')),
  },
])

const busy = () => chat.streamState === 'streaming'

const items = computed(() => {
  const q = mq.value.toLowerCase()
  if (menu.value === 'slash') {
    return CMD.value
      .filter((c) => c.key.slice(1).toLowerCase().includes(q) || c.desc.includes(mq.value))
      .map((c) => ({ label: c.key, desc: c.desc, tag: c.arg ? t('chat.tagArg') : '' }))
  }
  if (menu.value === 'at') {
    return skills.value
      .filter((s) => s.label.toLowerCase().includes(q) || (s.desc || '').includes(mq.value))
      .slice(0, 8)
  }
  return []
})

const hint = computed(() => (menu.value === 'slash' ? t('chat.hintSlash') : t('chat.hintAt')))

onMounted(async () => {
  grow()
  window.addEventListener('resize', onResize)
  try {
    const [a, b] = await Promise.all([
      getJSON('/ahs/md-skills').catch(() => ({})),
      getJSON('/ahs/skills').catch(() => ({})),
    ])
    skills.value = [
      ...(a.skills || [])
        .filter((s) => s.enabled !== false)
        .map((s) => ({ label: s.name, desc: s.description || '', tag: '技能' })),
      ...(b.skills || []).map((s) => ({
        label: s.name || s.id, desc: s.description || '', tag: s.kind || '工具',
      })),
    ]
  } catch {
  }
})

onBeforeUnmount(() => window.removeEventListener('resize', onResize))

function syncMenu() {
  const el = ta.value
  if (!el) return
  const pos = el.selectionStart ?? text.value.length
  const before = text.value.slice(0, pos)
  const mSlash = /(^|\s)\/([^\s/]*)$/.exec(before)
  if (mSlash) {
    if (menu.value !== 'slash') hover.value = 0
    menu.value = 'slash'
    mq.value = mSlash[2]
    return
  }
  const mAt = /(^|\s)@([^\s@]*)$/.exec(before)
  if (mAt) {
    if (menu.value !== 'at') hover.value = 0
    menu.value = 'at'
    mq.value = mAt[2]
    return
  }
  menu.value = null
}

watch(text, () => nextTick(syncMenu))

function closeMenu() {
  menu.value = null
}

function choose(i) {
  const it = items.value[i]
  if (!it) return
  const el = ta.value
  const pos = el ? el.selectionStart : text.value.length
  const before = text.value.slice(0, pos)

  if (menu.value === 'slash') {
    const cmd = CMD.find((c) => c.key === it.label)
    if (cmd && cmd.arg) {
      text.value = `${cmd.key} `
      closeMenu()
      nextTick(() => { el && el.focus(); grow() })
      return
    }
    text.value = ''
    closeMenu()
    nextTick(grow)
    if (cmd && cmd.run) cmd.run()
    return
  }

  const idx = before.lastIndexOf('@')
  const after = text.value.slice(pos)
  text.value = `${before.slice(0, idx)}@${it.label} ${after}`
  closeMenu()
  nextTick(() => { el && el.focus(); grow() })
}

function tryCommand(raw) {
  if (!raw.startsWith('/')) return false
  const [head, ...rest] = raw.split(/\s+/)
  const cmd = CMD.value.find((c) => c.key === head)
  if (!cmd) {
    say(t('chat.unknownCmd', { head }))
    return true
  }
  const arg = rest.join(' ').trim()
  if (cmd.key === '/搜索') runSearch(arg)
  else if (cmd.run) cmd.run()
  return true
}

function submit() {
  if (busy()) return stop()
  const t = text.value.trim()
  if (!t && !chat.attachments.length && !chat.images.length) return
  text.value = ''
  closeMenu()
  nextTick(grow) /* 清空后回缩到聚焦高度 */
  if (tryCommand(t)) return
  send(t)
}

const FOCUS_MIN = 84
const focused = ref(false)
function capH() {
  return Math.max(180, Math.min(340, Math.round(window.innerHeight * 0.42)))
}
function grow() {
  const el = ta.value
  if (!el) return
  el.style.height = 'auto'
  const need = el.scrollHeight
  const min = focused.value ? FOCUS_MIN : 0
  el.style.height = `${Math.min(Math.max(need, min), capH())}px`
}
function onFocus() {
  focused.value = true
  grow()
}
function onBlur() {
  focused.value = false
  grow()
}
function onResize() {
  grow()
}

function onKey(e) {
  if (menu.value && items.value.length) {
    const n = items.value.length
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      hover.value = (hover.value + 1) % n
      return
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      hover.value = (hover.value - 1 + n) % n
      return
    }
    if (e.key === 'Enter') {
      e.preventDefault()
      choose(hover.value)
      return
    }
    if (e.key === 'Escape') {
      e.preventDefault()
      closeMenu()
      return
    }
  }
  if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return
  e.preventDefault()
  submit()
}

function kb(n) {
  return n >= 1048576 ? `${(n / 1048576).toFixed(1)}M` : `${Math.max(1, Math.round(n / 1024))}K`
}
</script>

<template>
  <div class="dock">
    <div class="dock-in">
      <div v-if="chat.attachments.length || chat.images.length || chat.ingest" class="chips">
        <div v-for="(a, i) in chat.attachments" :key="a.id || i" class="chip" :title="`${a.name} · ${a.url}`">
          <span class="cn">{{ a.name }}</span>
          <span class="cs">{{ kb(a.size) }}</span>
          <span class="cx" @click="dropAttachment(i)">✕</span>
        </div>

        <div v-for="(im, i) in chat.images" :key="`img${i}`" class="chip img" :title="im.name">
          <img :src="im.url" :alt="im.name" />
          <span class="cx" @click="dropImage(i)">✕</span>
        </div>

        <div v-if="chat.ingest" class="chip ing" :class="chat.ingest.status">
          <span class="cn">{{ chat.ingest.name }}</span>
          <span class="cs">
            {{ chat.ingest.status === 'done' ? t('common.count', { n: chat.ingest.ku })
               : chat.ingest.status === 'error' ? t('chat.ingestFailed')
               : `${chat.ingest.pct}%` }}
          </span>
          <span class="bar"><i :style="{ width: `${chat.ingest.status === 'done' ? 100 : chat.ingest.pct}%` }" /></span>
          <span v-if="chat.ingest.status === 'done' || chat.ingest.status === 'error'" class="cx" @click="chat.ingest = null">✕</span>
        </div>
      </div>

      <ComposerMenu
        v-if="menu && items.length"
        :items="items"
        :index="hover"
        :hint="hint"
        @pick="choose"
        @hover="hover = $event"
      />

      <div class="wrap">
        <ToolMenu />
        <ModelPicker />
        <textarea
          ref="ta"
          v-model="text"
          rows="1"
          :placeholder="t('chat.placeholder')"
          @keydown="onKey"
          @input="grow"
          @focus="onFocus"
          @blur="onBlur"
          @click="syncMenu"
          @keyup="syncMenu"
        />
        <button class="send" :class="{ stop: busy() }" :title="busy() ? t('chat.stopGen') : t('chat.send')" @click="submit">
          {{ busy() ? '■' : '➤' }}
        </button>
      </div>
    </div>

    <Transition name="fade">
      <div v-if="chat.toast" class="toast">{{ chat.toast }}</div>
    </Transition>
  </div>
</template>

<style scoped>
.dock { padding: 0 8px 8px; position: relative; }

.dock-in {
  position: relative;
  width: min(736px, calc(100% - 44px));
  margin: 0 auto;
}

.chips { display: flex; gap: 6px; flex-wrap: wrap; padding: 0 2px 7px; }
.chip {
  display: flex; align-items: center; gap: 6px;
  padding: 5px 9px; border-radius: var(--r-pill);
  font-size: 11.5px;
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid var(--line);
  box-shadow: 0 1px 2px rgba(23, 32, 26, 0.04);
  max-width: 220px;
}
.chip .cn { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink); }
.chip .cs { font-family: var(--mono); font-size: 10.5px; color: var(--dim); flex-shrink: 0; }
.chip .cx { cursor: pointer; font-size: 11px; color: var(--dim); padding: 0 2px; flex-shrink: 0; }
.chip .cx:hover { color: var(--red); }
.chip.img { padding: 3px 5px; }
.chip.img img { width: 22px; height: 22px; border-radius: 5px; object-fit: cover; }

.chip.ing { border-color: rgba(26, 157, 95, 0.3); }
.chip.ing.error { border-color: rgba(200, 60, 60, 0.34); }
.chip.ing .bar {
  width: 44px; height: 3px; border-radius: 2px; overflow: hidden;
  background: rgba(23, 32, 26, 0.1); flex-shrink: 0;
}
.chip.ing .bar i { display: block; height: 100%; background: var(--green); transition: width 0.3s; }
.chip.ing.error .bar i { background: var(--red); }

.wrap {
  display: flex; align-items: flex-end; gap: 8px;
  padding: 7px 9px;
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid var(--line-soft);
  border-radius: var(--r-mid);
  box-shadow: var(--sh-card);
  transition: border-color 0.15s, box-shadow 0.15s;
}
.wrap:focus-within {
  border-color: rgba(26, 157, 95, 0.4);
  box-shadow: 0 0 0 3px rgba(26, 157, 95, 0.08), var(--sh-card);
}
textarea {
  flex: 1; min-width: 0;
  font-size: 13.5px; line-height: 1.6;
  padding: 5px 2px; border: 0; background: none;
  resize: none; overflow-y: auto;
  max-height: 340px;
  transition: height 0.14s ease;
}
textarea:focus { outline: none; }
textarea::placeholder { color: var(--dim); opacity: 1; }

.send {
  width: 30px; height: 30px; border-radius: 50%; flex-shrink: 0;
  display: grid; place-items: center;
  font-size: 12px; color: #fff;
  background: var(--green);
  box-shadow: 0 4px 12px rgba(26, 157, 95, 0.3);
  transition: transform 0.1s, filter 0.15s, background 0.15s;
}
.send:hover { filter: brightness(1.06); }
.send:active { transform: scale(0.94); }
.send.stop { background: var(--red); box-shadow: 0 4px 12px rgba(229, 72, 77, 0.28); font-size: 10px; }

.toast {
  position: absolute; left: 50%; bottom: 62px; transform: translateX(-50%);
  padding: 7px 13px; border-radius: var(--r-pill);
  font-size: 11.5px; color: var(--cli-ink);
  background: rgba(17, 22, 15, 0.92);
  box-shadow: var(--sh-pop); white-space: nowrap;
  max-width: 82vw; overflow: hidden; text-overflow: ellipsis;
  z-index: 30;
}
.fade-enter-active, .fade-leave-active { transition: opacity 0.18s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
