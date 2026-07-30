import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// In development, Vite serves the UI on 5173 and proxies API and WebSocket traffic to the
// Python server on 8080. In production the Python server serves the built assets itself, so
// there is no proxy and no second process.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      // Both the REST API and the WebSockets live under /api/v1 (`/api/v1/ws/jobs` and
      // `/api/v1/chat/ws/{id}`), so a single rule covers them — but it needs `ws: true`,
      // otherwise Vite never registers an upgrade handler and job progress and streaming
      // chat silently fail in dev.
      '/api': { target: 'http://127.0.0.1:8080', changeOrigin: true, ws: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        // Mermaid and the editor are large and not needed on first paint; splitting them
        // keeps the initial bundle small enough that the dashboard renders quickly.
        // `@tiptap/pm` is deliberately absent: it exposes only subpath exports
        // (`@tiptap/pm/state` and friends) and has no `.` entry, so naming it here fails
        // resolution. Its submodules land in the editor chunk with the rest of TipTap anyway.
        manualChunks: {
          editor: ['@tiptap/react', '@tiptap/starter-kit'],
          mermaid: ['mermaid'],
          vendor: ['react', 'react-dom', '@tanstack/react-router', '@tanstack/react-query'],
        },
      },
    },
  },
})
