import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],

  // Dev server: proxy all /api requests to the FastAPI backend
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },

  // Production build: output into api/static/ so FastAPI serves it
  build: {
    outDir: '../api/static',
    emptyOutDir: true,
  },
});
