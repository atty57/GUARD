import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // "@/components/x" -> "src/components/x" (same alias as tsconfig)
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    proxy: {
      // 127.0.0.1, not localhost: avoids slow/failed IPv6 lookups that can stall SSE (PLAN.md §11)
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
