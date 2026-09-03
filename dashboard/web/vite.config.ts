import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 화면은 Vercel, API는 NCP 서버(Tailscale Funnel 경유)로 따로 배포한다(08-dashboard 8.5).
// 개발 중에는 같은 출처로 보이게 프록시를 태워, 쿠키가 브라우저 정책에 걸리지 않게 한다.
const API_TARGET = process.env.VITE_DEV_API ?? 'http://127.0.0.1:8787'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: { '/api': { target: API_TARGET, changeOrigin: true } },
  },
})
