import { BrowserRouter, Routes, Route } from "react-router-dom";
import { HandSocketProvider } from "./context/HandSocketContext";
import Layout from "./layouts/Layout";
import Home from "./pages/Home";
import VisionMode from "./pages/VisionMode";
import OrderMode from "./pages/OrderMode";

export default function App() {
  return (
    // 전체 앱에서 웹소켓 연결을 하나만 유지 (페이지 이동해도 재연결되지 않음)
    <HandSocketProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<Home />} />
            <Route path="/vision" element={<VisionMode />} />
            <Route path="/order" element={<OrderMode />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </HandSocketProvider>
  );
}
