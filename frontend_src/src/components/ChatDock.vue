<script setup>
import { ref } from 'vue'

const text = ref('')
const toast = ref('')

function submit() {
  if (!text.value.trim()) return
  text.value = ''
  toast.value = '对话链路在 P1 接入 SSE(/ws/harness),当前为 P0 骨架'
  setTimeout(() => (toast.value = ''), 2400)
}
</script>

<template>
  <div class="dock">
    <div class="wrap">
      <button class="plus" title="工具:模式 / 上传 / 文件夹 / 图片 / 连接器（P1）">＋</button>
      <div class="mpill" title="选择对话模型（P1）">
        <span class="mdot" /> Auto <span class="caret">▾</span>
      </div>
      <input
        v-model="text"
        placeholder="告诉我你想做什么…  主管 Agent 将自动调度"
        @keydown.enter="submit"
      />
      <button class="send" title="发送" @click="submit">➤</button>
    </div>
    <Transition name="fade">
      <div v-if="toast" class="toast">{{ toast }}</div>
    </Transition>
  </div>
</template>

<style scoped>
.dock { padding: 0 8px 8px; position: relative; }
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
.plus {
  width: 28px; height: 28px; border-radius: 50%; flex-shrink: 0;
  display: grid; place-items: center;
  font-size: 15px; color: var(--dim);
  background: var(--surface-2);
}
.plus:hover { color: var(--ink); background: var(--surface-3); }
.mpill {
  display: flex; align-items: center; gap: 5px; flex-shrink: 0;
  padding: 5px 9px; border-radius: var(--r-pill);
  font-size: 11.5px; color: var(--dim);
  background: var(--surface-2);
  cursor: pointer;
}
.mpill .mdot { width: 5px; height: 5px; border-radius: 50%; background: var(--green); }
.mpill .caret { font-size: 8px; color: var(--faint); }
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
}
.fade-enter-active, .fade-leave-active { transition: opacity 0.18s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
