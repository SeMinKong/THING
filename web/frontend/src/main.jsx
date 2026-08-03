import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";

// FR-28(내부망 전용) 요구사항에 따라 npm 패키지로 설치된 로컬 Bootstrap 의존성 로드
import "bootstrap/dist/css/bootstrap.min.css";
import "bootstrap/dist/js/bootstrap.bundle.min.js";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);