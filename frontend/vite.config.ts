import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // 开发期把 /api 代理到本地后端（生产由 FastAPI 同源托管，无需代理）
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
    // Windows 下编辑器/工具的原子写文件偶发不触发 watcher（HMR 报了 update 但转换缓存不失效，
    // 浏览器一直拿到旧模块）。轮询兜底，代价是少量 CPU。
    watch: { usePolling: true, interval: 500 },
  },
})
