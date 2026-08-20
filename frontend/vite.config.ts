import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // 开发期把 /api 代理到本地后端（生产由 FastAPI 同源托管，无需代理）
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
})
