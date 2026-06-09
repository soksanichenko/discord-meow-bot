import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    // In local dev the Discord proxy doesn't exist, so Vite replicates it:
    // requests to /api/* are forwarded to the Python backend on port 8082
    // with the /api prefix stripped — matching what Discord's proxy does in prod.
    proxy: {
      '/api': {
        target: 'http://localhost:8082',
        ws: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
});
