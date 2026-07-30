import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],

  // 로컬 개발 시 Vite dev 서버(5173)에서 /api 를 Django(8000)로 프록시한다.
  // 이렇게 하면 개발 환경에서도 배포와 동일하게 상대경로로 동작한다.
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },

  // 완료 조건: "모바일·Laptop 기본 화면과 오류 fixture 테스트가 통과한다"
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.js',
    css: true,
  },
})
