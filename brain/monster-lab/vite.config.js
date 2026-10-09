// Dev server proxies the Frankenstein API so the browser needs no CORS setup:
//   /frank/*  →  $FRANK_URL (default http://localhost:8000)
export default {
  server: {
    proxy: {
      '/frank': {
        target: process.env.FRANK_URL || 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/frank/, ''),
      },
    },
  },
  build: { chunkSizeWarningLimit: 1200 },
};
