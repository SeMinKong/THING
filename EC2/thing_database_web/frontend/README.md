# React + Vite (frontend)

EC2 데이터 포털의 프런트엔드입니다. 기존 Vue 3 구현을 React 19 + React Router 7로 교체했습니다.

## 실행

```bash
npm install
npm run dev      # 로컬 개발 서버 (http://localhost:5173)
npm run build    # 배포용 정적 파일 생성 -> dist/
```

`npm run dev`는 `vite.config.js`의 proxy 설정으로 `/api`와 `/media`를
로컬 Django(`127.0.0.1:8000`)로 전달합니다. 따라서 개발 환경에서도 배포와 동일하게
상대경로만 사용하면 됩니다.

## 구조

```
src/
├─ main.jsx                  # 엔트리 (BrowserRouter 주입)
├─ App.jsx                   # 네비게이션 바 + 라우트 정의
├─ index.css                 # 전역 스타일
├─ services/
│  └─ api.js                 # axios 인스턴스
└─ views/
   ├─ HomeView.jsx           # 랜딩
   └─ DataDownloadView.jsx   # 목록 조회 + 다운로드
```

## 배포

`npm run build` 산출물인 `dist/`를 Nginx가 직접 서빙합니다.
경로는 `deploy/nginx_thing_database_web.conf`의 `root` 지시어를 참고하세요.
