import path from "path"
import { defineConfig, type ProxyOptions } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const BACKEND = 'http://localhost:8420'

const proxyOpts: ProxyOptions = { target: BACKEND, changeOrigin: true }

// WS proxies: silence EPIPE/ECONNRESET errors from broken socket connections.
// The client-side reconnects automatically via exponential backoff.
const wsProxyOpts: ProxyOptions = {
  target: BACKEND,
  changeOrigin: true,
  ws: true,
  configure: (proxy) => {
    // Suppress EPIPE/ECONNRESET from closed WS connections (e.g. token rejection).
    // Client reconnects automatically.
    proxy.on('error', () => {})
    proxy.on('proxyReqWs', (_proxyReq, _req, socket) => {
      socket.on('error', () => {})
    })
  },
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  base: "/",
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          xterm: ["@xterm/xterm", "@xterm/addon-fit", "@xterm/addon-web-links"],
          vendor: ["react", "react-dom", "react-router", "zustand"],
        },
      },
    },
  },
  server: {
    proxy: {
      '/projects': proxyOpts,
      '/processes': proxyOpts,
      '/tasks': proxyOpts,
      '/collections': proxyOpts,
      '/agents': proxyOpts,
      '/activity': proxyOpts,
      '/screenshots': proxyOpts,
      '/context': proxyOpts,
      '/browser': proxyOpts,
      '/orchestrator': proxyOpts,
      '/chat': proxyOpts,
      '/ports': proxyOpts,
      '/admin': proxyOpts,
      '/config': proxyOpts,
      '/voice': proxyOpts,
      '/tts': proxyOpts,
      '/browse': proxyOpts,
      '/tokens': proxyOpts,
      '/health': proxyOpts,
      '/status': proxyOpts,
      '/state': proxyOpts,
      '/auth': proxyOpts,
      '/old': proxyOpts,
      '/recordings': proxyOpts,
      '/settings': proxyOpts,
      '/events': proxyOpts,
      '/audit': proxyOpts,
      '/recipes': proxyOpts,
      '/static': proxyOpts,
      '/ws': wsProxyOpts,
      '/stt': wsProxyOpts,
      '/terminals': wsProxyOpts,
    },
  },
})
