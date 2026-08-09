// frontend/src/test/setup.js
import "@testing-library/jest-dom/vitest";

// jsdom 에는 WebSocket 이 없다. 테스트에서 서버 없이 프로토콜을 검증하려면
// 직접 제어할 수 있는 대역이 필요하다.
//
// 실제 WebSocket 을 흉내내되 테스트가 다음을 할 수 있게 한다.
//   - 서버가 보낸 것처럼 메시지를 밀어넣기        emit(payload)
//   - 클라이언트가 보낸 것을 확인하기             sent 배열
//   - 연결·해제를 임의 시점에 발생시키기          open() / closeFromServer()
class MockWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  static instances = [];

  constructor(url) {
    this.url = url;
    this.readyState = MockWebSocket.OPEN;
    this.sent = [];
    this.onopen = null;
    this.onmessage = null;
    this.onerror = null;
    this.onclose = null;
    MockWebSocket.instances.push(this);
    // 즉시 open 하지 않는다. 테스트가 open() 을 불러 시점을 정한다.
  }

  send(data) {
    this.sent.push(JSON.parse(data));
  }

  close() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({ code: 1000 });
  }

  // ── 테스트용 ──

  /** 서버 연결이 열린 것으로 만든다. */
  open() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.({});
  }

  /** 서버가 메시지를 보낸 것으로 만든다. */
  emit(payload) {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }

  /** 서버 쪽에서 끊긴 것으로 만든다. */
  closeFromServer(code = 1006) {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({ code });
  }

  /** 클라이언트가 보낸 요청 중 특정 type 만 고른다. */
  sentOf(type) {
    return this.sent.filter((m) => m.type === type);
  }

  static latest() {
    return MockWebSocket.instances[MockWebSocket.instances.length - 1];
  }

  static reset() {
    MockWebSocket.instances = [];
  }
}

global.WebSocket = MockWebSocket;
global.MockWebSocket = MockWebSocket;

// import.meta.env.VITE_WS_URL 은 테스트에서 비워 기본 경로를 쓰게 한다.
