<script setup>
import TopBar from './components/TopBar.vue'
import LeftRail from './components/LeftRail.vue'
import Stage from './components/Stage.vue'
import ChatDock from './components/ChatDock.vue'
import Inspector from './components/Inspector.vue'
import { state } from './stores/app.js'
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
    <button v-if="!state.railOpen" class="rail-handle" title="展开检视栏" @click="state.railOpen = true">
      检视
    </button>
  </div>
</template>
