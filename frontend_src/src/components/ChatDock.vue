<script setup>
import { nextTick, ref, watch } from 'vue'
import ModelPicker from './ModelPicker.vue'
import ToolMenu from './ToolMenu.vue'
import { chat, dropAttachment, dropImage, send, stop } from '../stores/chat.js'

const text = ref('')
const ta = ref(null)

const busy = () => chat.streamState === 'streaming'

function submit() {
  if (busy()) return stop()
  const t = text.value
  if (!t.trim() && !chat.attachments.length && !chat.images.length) return
  text.value = ''
  send(t)
}

/* textarea 自适应 1~4 行,超出内部滚动 */
function grow() {
  const el = ta.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 112)}px`
}
watch(text, () => nextTick(grow))

function onKey(e) {
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
          {{ chat.ingest.status === 'done' ? `${chat.ingest.ku} 条`
             : chat.ingest.status === 'error' ? '失败'
             : `${chat.ingest.pct}%` }}
        </span>
        <span class="bar"><i :style="{ width: `${chat.ingest.status === 'done' ? 100 : chat.ingest.pct}%` }" /></span>
        <span v-if="chat.ingest.status === 'done' || chat.ingest.status === 'error'" class="cx" @click="chat.ingest = null">✕</span>
      </div>
    </div>

    <div class="wrap">
      <ToolMenu />
      <ModelPicker />
      <textarea
        ref="ta"
        v-model="text"
        rows="1"
        placeholder="告诉我你想做什么…  Enter 发送 · Shift+Enter 换行"
        @keydown="onKey"
        @input="grow"
      />
      <button class="send" :class="{ stop: busy() }" :title="busy() ? '停止生成' : '发送'" @click="submit">
        {{ busy() ? '■' : '➤' }}
      </button>
    </div>

    <Transition name="fade">
      <div v-if="chat.toast" class="toast">{{ chat.toast }}</div>
    </Transition>
  </div>
</template>

<style scoped>
.dock { padding: 0 8px 8px; position: relative; }

.chips { display: flex; gap: 6px; flex-wrap: wrap; padding: 0 2px 7px; }
.chip {
  display: flex; align-items: center; gap: 6px;
  padding: 4px 8px; border-radius: var(--r-pill);
  font-size: 11px;
  background: rgba(255, 255, 255, 0.88);
  border: 1px solid var(--line-soft);
  box-shadow: 0 1px 2px rgba(23, 32, 26, 0.04);
  max-width: 220px;
}
.chip .cn { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink); }
.chip .cs { font-family: var(--mono); font-size: 9.5px; color: var(--faint); flex-shrink: 0; }
.chip .cx { cursor: pointer; font-size: 9px; color: var(--faint); padding: 0 1px; flex-shrink: 0; }
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
  font-size: 13px; line-height: 1.6;
  padding: 5px 2px; border: 0; background: none;
  resize: none; overflow-y: auto;
  max-height: 112px;
}
textarea::placeholder { color: var(--faint); }

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
