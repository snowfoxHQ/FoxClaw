import { execSync } from 'node:child_process'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

function gitInfo() {
  try {
    const sha = execSync('git rev-parse --short HEAD', { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim()
    const ts = Number(execSync('git log -1 --format=%ct', { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim()) * 1000
    return { sha, ts: Number.isFinite(ts) ? ts : 0 }
  } catch {
    return { sha: 'dev', ts: 0 }
  }
}

export default defineConfig({
  base: '/v3/',
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  define: {
    __APP_INFO__: JSON.stringify({ git: gitInfo(), built: Date.now() }),
  },
  build: {
    outDir: '../frontend/v3',
    emptyOutDir: true,
    target: 'es2022',
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
      '/planet': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/knowledge': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
