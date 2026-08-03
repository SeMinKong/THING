import { Link } from "react-router-dom";
import { useEffect, useRef } from "react";
import { animate, stagger, splitText } from 'animejs'; // npm install animejs 후 사용

export default function Home() {
  const h1Ref = useRef(null);

  useEffect(() => {
    if (!h1Ref.current) return;

    const { chars } = splitText(h1Ref.current, { words: false, chars: true });

    const animation = animate(chars, {
      y: [
        { to: '-2.75rem', ease: 'outExpo', duration: 600 },
        { to: 0, ease: 'outBounce', duration: 800, delay: 100 }
      ],
      rotate: { from: '-1turn', delay: 0 },
      delay: stagger(50),
      ease: 'inOutCirc',
      loopDelay: 1000,
      loop: true
    });

    return () => {
      animation.pause(); // 또는 revert() 등 정리
    };
  }, []);

  return (
    <div
      className="container d-flex align-items-center justify-content-center"
      style={{ minHeight: "60vh", marginTop: "10vh" }}
    >
      <div className="text-center">
        <h1 ref={h1Ref} className="display-3 fw-bold mb-5 tracking-tight text-dark">
          환영합니다
        </h1>

        <div className="d-flex justify-content-center gap-4">
          <Link
            to="/vision"
            className="btn btn-outline-dark btn-lg px-5 py-3 rounded-pill fw-semibold shadow-sm"
          >
            손 모방 페이지
          </Link>
          <Link
            to="/order"
            className="btn btn-outline-dark btn-lg px-5 py-3 rounded-pill fw-semibold shadow-sm"
          >
            명령 제공 페이지
          </Link>
        </div>
      </div>
    </div>
  );
}