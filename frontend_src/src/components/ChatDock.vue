<script setup>
import { ref } from 'vue'
import ModelPicker from './ModelPicker.vue'
import ToolMenu from './ToolMenu.vue'
import { chat, dropAttachment, dropImage, say } from '../stores/chat.js'

const text = ref('')

function submit() {
  const has = text.value.trim() || chat.attachments.length || chat.images.length
  if (!has) return
  say('对话链路在 P1 接 SSE(/ws/harness);附件与图片已就绪,会随消息一起发出')
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
      <input
        v-model="text"
        placeholder="告诉我你想做什么…  主管 Agent 将自动调度"
        @keydown.enter="submit"
      />
      <button class="send" title="发送" @click="submit">➤</button>
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
  display: flex; align-items: center; gap: 8px;
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
input { flex: 1; min-width: 0; font-size: 13px; padding: 4px 2px; }
input::placeholder { color: var(--faint); }
.send {
  width: 30px; height: 30px; border-radius: 50%; flex-shrink: 0;
  display: grid; place-items: center;
  font-size: 12px; color: #fff;
  background: var(--green);
  box-shadow: 0 4px 12px rgba(26, 157, 95, 0.3);
  transition: transform 0.1s, filter 0.15s;
}
.send:hover { filter: brightness(1.06); }
.send:active { transform: scale(0.94); }

.toast {
  position: absolute; left: 50%; bottom: 62px; transform: translateX(-50%);
  padding: 7px 13px; border-radius: var(--r-pill);
  font-size: 11.5px; color: var(--cli-ink);
  background: rgba(17, 22, 15, 0.92);
  box-shadow: var(--sh-pop); white-space: nowrap;
  max-width: 82vw; overflow: hidden; text-overflow: ellipsis;
}
.fade-enter-active, .fade-leave-active { transition: opacity 0.18s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
