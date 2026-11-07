<script setup>
import TopBar from './components/TopBar.vue'
import LeftRail from './components/LeftRail.vue'
import Stage from './components/Stage.vue'
import ChatDock from './components/ChatDock.vue'
import Inspector from './components/Inspector.vue'
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { state, setModule } from './stores/app.js'
import { bootChat } from './stores/chat.js'

/* 窄窗断点与 base.css 的 @media (max-width: 1180px) 是同一个数:
   到这条线以下右栏变成抽屉(见 base.css),这里负责「变窄时自动收起」。
   否则 900px 窗口里三栏并存,中栏只剩 252px,输入框被 ＋ / 模型胶囊挤到 115px。 */
const NARROW = 1180
const narrow = ref(false)

function syncNarrow() {
  const n = window.innerWidth < NARROW
  if (n === narrow.value) return
  narrow.value = n
  if (n) state.railOpen = false
}

/* 恢复上次会话历史(后端落盘) */
onMounted(() => {
  bootChat()
  narrow.value = window.innerWidth < NARROW
  if (narrow.value) state.railOpen = false
  window.addEventListener('keydown', onKey)
  window.addEventListener('resize', syncNarrow)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('resize', syncNarrow)
})

/* Ctrl+, 打开设置(顶栏 ⚙ 旁边写的那个键位)。输入框里也生效 —— 它是全局入口 */
function onKey(e) {
  if (e.ctrlKey && (e.key === ',' || e.code === 'Comma')) {
    e.preventDefault()
    setModule('settings')
  }
}
</script>

<template>
  <div class="app">
    <TopBar />
    <div class="workspace" :class="{ 'is-collapsed': !state.railOpen }">
      <LeftRail />
      <main class="col col--mid mid">
        <Stage />
        <ChatDock />
      </main>
      <Inspector />
    </div>
    <div v-if="narrow && state.railOpen" class="rail-scrim" @click="state.railOpen = false" />
    <button v-if="!state.railOpen" class="rail-handle" title="展开检视栏" @click="state.railOpen = true">
      检视
    </button>
  </div>
</template>
