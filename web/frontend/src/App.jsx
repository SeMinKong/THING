import { BrowserRouter, Routes, Route } from "react-router-dom";
import { HandSocketProvider } from "./context/HandSocketContext";
import { ModeGateProvider } from "./components/ModeGate";
import Layout from "./layouts/Layout";
import Home from "./pages/Home";
import VisionMode from "./pages/VisionMode";
import OrderMode from "./pages/OrderMode";

export default function App() {
  return (
    // 전체 앱에서 웹소켓 연결을 하나만 유지 (페이지 이동해도 재연결되지 않음)
    <HandSocketProvider>
      <BrowserRouter>
        {/* 제어권 획득·해제를 화면 이동에 묶는다. 모달이 여기서 뜬다 */}
        <ModeGateProvider>
          <Routes>
            {/* 개요는 셸 밖이다. 상태 머리·안전 알림 없이 문구와 버튼만 둔다 */}
            <Route path="/" element={<Home />} />
            <Route element={<Layout />}>
              <Route path="/vision" element={<VisionMode />} />
              <Route path="/order" element={<OrderMode />} />
            </Route>
          </Routes>
        </ModeGateProvider>
      </BrowserRouter>
    </HandSocketProvider>
  );
}
