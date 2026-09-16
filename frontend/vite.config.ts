import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [react()],
    server: {
      host: '127.0.0.1', port: 5174, strictPort: true,
      proxy: {
        '/api': { target: env.BACKEND_URL || 'http://127.0.0.1:8000', changeOrigin: true },
      },
    },
  }
})
