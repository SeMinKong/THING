import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";

// FR-28: 내부망 전용이므로 외부 CDN 을 쓰지 않는다.
// 폰트는 @fontsource 로 번들에 포함되며 console.css 가 불러온다.
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")).render(
    <App />
);
