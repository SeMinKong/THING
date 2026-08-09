// frontend/src/views/HomeView.jsx
import { Link } from 'react-router-dom';
import { motion } from 'motion/react';
import { ArrowRight } from 'lucide-react';

import { draw, rise } from '../ui/motion';

/**
 * 히어로 그래픽.
 *
 * 로봇 이모지 하나보다 이 그림이 낫다고 본 이유: 이 아카이브에 쌓이는 것은
 * 7개 논리축이 시간에 따라 변한 기록이다. 서로 다른 위상으로 흐르는 곡선이
 * 그 사실을 한 문장보다 빠르게 전달한다.
 *
 * stroke 를 실제로 그려내되(pathLength) 1.15초 안에 끝낸다. 장식이 본문을
 * 기다리게 만들면 안 된다.
 */
function SignalFigure() {
  const W = 1000;
  const H = 168;
  const curves = [
    { phase: 0.0, amp: 30, cycles: 2.0, stroke: 'var(--accent)', opacity: 1 },
    { phase: 1.2, amp: 22, cycles: 2.7, stroke: 'var(--accent)', opacity: 0.5 },
    { phase: 2.4, amp: 15, cycles: 1.6, stroke: 'var(--ink-3)', opacity: 0.55 },
    { phase: 3.6, amp: 10, cycles: 3.4, stroke: 'var(--ink-3)', opacity: 0.32 },
  ];

  const path = ({ phase, amp, cycles }) => {
    const pts = [];
    for (let i = 0; i <= 80; i += 1) {
      const t = i / 80;
      // 양 끝의 진폭을 좁혀 리본처럼 만든다. 잘린 느낌을 없앤다.
      const envelope = Math.sin(t * Math.PI) ** 0.6;
      const y = H / 2 + Math.sin(t * Math.PI * cycles + phase) * amp * envelope;
      pts.push(`${(t * W).toFixed(1)},${y.toFixed(1)}`);
    }
    return `M${pts.join('L')}`;
  };

  return (
    <div className="hero-figure" aria-hidden="true">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        {/* 기준선. 계측 그래프의 0 축을 암시한다 */}
        <line x1="0" y1={H / 2} x2={W} y2={H / 2} stroke="var(--rule-soft)" strokeWidth="1" />
        {curves.map((curve, index) => (
          <motion.path
            key={index}
            d={path(curve)}
            fill="none"
            stroke={curve.stroke}
            strokeOpacity={curve.opacity}
            strokeWidth="1.6"
            strokeLinecap="round"
            {...draw(index * 0.1)}
          />
        ))}
      </svg>
    </div>
  );
}

export default function HomeView() {
  return (
    <main className="sheet narrow hero">
      <div className="hero">
        <SignalFigure />

        <motion.p className="eyebrow" {...rise(0.05)}>
          Public data archive
        </motion.p>

        <motion.h1 {...rise(0.1)}>
          텐던 구동 로봇 핸드의 계측 기록 아카이브
        </motion.h1>

<motion.p className="lede" {...rise(0.18)}>
  <strong style={{ fontSize: '1.5rem' }}>T</strong>endon-driven robot <strong style={{ fontSize: '1.5rem' }}>H</strong>and with <strong style={{ fontSize: '1.5rem' }}>I</strong>ntelligent <strong style={{ fontSize: '1.5rem' }}>N</strong>eural <strong style={{ fontSize: '1.5rem' }}>G</strong>rasp
</motion.p>


        <motion.dl className="hero-facts" {...rise(0.26)}>
          <div className="hero-fact">
            <dt>기록 축</dt>
            <dd>7<small>축</small></dd>
          </div>
          <div className="hero-fact">
            <dt>다운로드 가능한 데이터</dt>
            <dd>4<small>종류</small></dd>
          </div>
          <div className="hero-fact">
            <dt>시각 기준</dt>
            <dd>UTC</dd>
          </div>
        </motion.dl>

        <motion.div className="btn-row" {...rise(0.32)}>
          <Link to="/sessions" className="btn btn-primary btn-lg">
            세션 목록 보기
            <ArrowRight size={17} strokeWidth={2} />
          </Link>
        </motion.div>
      </div>
    </main>
  );
}
