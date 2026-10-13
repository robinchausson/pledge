import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    proxy: {
      '/auth': {
        target: process.env.VITE_BACKEND_INTERNAL_URL || 'http://backend:8000',
        changeOrigin: true,
      },
      '/api': {
        target: process.env.VITE_BACKEND_INTERNAL_URL || 'http://backend:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
