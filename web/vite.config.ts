import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // 127.0.0.1, not localhost: avoids slow/failed IPv6 lookups that can stall SSE (PLAN.md §11)
      '/api': 'http://127.0.0.1:8000',
    },
  },
})