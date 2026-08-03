// frontend/src/main.jsx
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

// 폰트는 자가 호스팅한다.
// 이 사이트는 EC2 에서 nginx 가 정적 파일을 직접 내보내는 구성이다. 외부 CDN 을
// 물면 그 CDN 이 느려질 때 한글이 나중에 그려지면서 화면이 한 번 튄다(FOUT).
// dynamic-subset 은 실제로 쓰인 글자만 내려받아 한글 웹폰트의 용량 문제를 피한다.
import 'pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css';
import '@fontsource-variable/jetbrains-mono';

import App from './App.jsx';
import './index.css';

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
