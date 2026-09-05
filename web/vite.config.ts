import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  // Listen on IPv4 and IPv6 so both localhost and 127.0.0.1 work.
  server: { host: '0.0.0.0', port: 5173 },
})
