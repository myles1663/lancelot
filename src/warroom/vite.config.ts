import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  base: '/war-room/',
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
      '/connectors': 'http://localhost:8000',
      '/chat': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/soul': 'http://localhost:8000',
      '/memory': 'http://localhost:8000',
      '/system': 'http://localhost:8000',
      '/usage': 'http://localhost:8000',
      '/onboarding': 'http://localhost:8000',
      '/crusader_status': 'http://localhost:8000',
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
      },
    },
  },
})
