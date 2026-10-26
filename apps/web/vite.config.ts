import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 部署到子路径（如 https://域名/xz/）时用 VITE_BASE=/xz/ 构建；
// 默认 '/' 保持本地开发与根域名部署行为不变。
export default defineConfig({
  base: process.env.VITE_BASE || '/',
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8787',
        changeOrigin: true,
      },
    },
  },
})
