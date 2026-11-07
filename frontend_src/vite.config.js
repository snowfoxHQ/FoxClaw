import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

/* 产物直接写进 frontend/v3/,后端 (StaticFiles(directory=frontend, html=True)) 零改动:
   /v3/ -> frontend/v3/index.html
   base 必须是 '/v3/',否则 assets 会打到站点根。 */
export default defineConfig({
  base: '/v3/',
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    outDir: '../frontend/v3',
    emptyOutDir: true,
    target: 'es2022',
  },
  server: {
    port: 5173,
    /* dev 时把 API 与 WS 透到本地后端,前端代码里一律用相对路径 /api */
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
      '/planet': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      /* /knowledge/* 无 /api 前缀:HKC 自带 router 由后端根路径挂载 */
      '/knowledge': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
