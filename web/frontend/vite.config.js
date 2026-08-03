import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// FR-28: frontend 는 Laptop 에서 native Vite + React 로 실행하고
// WS_URL·MJPEG_URL 외부 설정으로 Jetson Web Bridge 와 MJPEG 에 연결한다.
//
// Django 로 빌드 산출물을 서빙하던 구성은 없어졌다. 브라우저는 Jetson 의
// thing_web_bridge 노드(/ws/robot-state)에 직접 붙는다 (6.4절).
export default defineConfig({
  plugins: [react(), tailwindcss()],

  server: {
    // 로컬 개발 편의용 프록시.
    //
    // VITE_WS_URL 을 지정하면 프런트가 그 절대 주소로 직접 붙으므로 이 프록시는
    // 쓰이지 않는다. 지정하지 않은 경우에만 현재 호스트의 /ws/robot-state 로
    // 붙게 되고, 그때 아래 target 으로 넘어간다.
    //
    // 기본값은 같은 장비에서 브릿지 노드를 띄운 경우를 가정한다.
    // Jetson 에 붙을 때는 VITE_WS_URL 을 쓰는 편이 낫다 (env.txt 참조).
    proxy: {
      "/ws/robot-state": {
        target: process.env.VITE_DEV_WS_TARGET || "ws://localhost:8000",
        ws: true,
      },
    },
  },

  // 프런트엔드 회귀 테스트.
  // 브릿지 계약 테스트는 thing_ws/src/thing_web_bridge/test/ 에 있다.
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.js",
    css: true,
    // 기본 5000ms 는 lease 갱신 fake timer 검증에 충분하다.
    testTimeout: 10000,
  },
});
