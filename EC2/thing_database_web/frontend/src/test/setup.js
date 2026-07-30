// frontend/src/test/setup.js
import '@testing-library/jest-dom/vitest';

// recharts 의 ResponsiveContainer 는 부모 크기를 측정해 렌더한다.
// jsdom 은 레이아웃을 계산하지 않아 크기가 0 이 되고 차트가 그려지지 않는다.
// 테스트에서 차트 유무를 확인할 수 있도록 측정 API 를 고정 크기로 대체한다.
global.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
  configurable: true,
  value: 800,
});
Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
  configurable: true,
  value: 320,
});
