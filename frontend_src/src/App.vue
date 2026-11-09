<script setup>
import TopBar from './components/TopBar.vue'
import LeftRail from './components/LeftRail.vue'
import Stage from './components/Stage.vue'
import ChatDock from './components/ChatDock.vue'
import Inspector from './components/Inspector.vue'
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { state, setModule } from './stores/app.js'
import { bootChat } from './stores/chat.js'

const NARROW = 1180
const narrow = ref(false)

function syncNarrow() {
  const n = window.innerWidth < NARROW
  if (n === narrow.value) return
  narrow.value = n
  if (n) state.railOpen = false
}

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
