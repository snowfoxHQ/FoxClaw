import { createApp } from 'vue'
import App from './App.vue'
import './styles/tokens.css'
import './styles/base.css'
import './styles/panel.css'
import { applyLangTag } from './lib/i18n.js'

applyLangTag()
createApp(App).mount('#app')
