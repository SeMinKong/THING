# thing_logger exporter 출력 계약

이 문서는 `thing_logger`의 현재 canonical exporter 구현과 일치하는 공개
파일 계약을 기록한다. 이 계약은 완료된 rosbag2 세션에서 생성하는
`session_{session_id}_landmark.json`에 적용한다.

## LandMark JSON

- 파일명: `session_{session_id}_landmark.json`
- 인코딩: UTF-8 JSON
- 최상위 값: Landmark 레코드 배열
- Landmark 메시지가 없던 세션은 빈 배열 `[]`로 표현한다.
- metadata의 `files.landmark.row_count`는 최상위 배열의 레코드 수다.

각 배열 원소는 기록된 `HandLandmarks` 메시지 한 건이며, 아래 필드 순서와
형태를 사용한다.

```json
{
  "session_id": "123",
  "timestamp": "1970-01-01T00:00:10.750Z",
  "stamp_sec": 10,
  "stamp_nanosec": 750000000,
  "elapsed_ms": 750,
  "detected": true,
  "confidence": 0.95,
  "handedness": 2,
  "handedness_confidence": 0.98,
  "image_width": 640,
  "image_height": 480,
  "landmarks": [
    {"x": 0.0, "y": 0.2, "z": -0.01}
  ]
}
```

`landmarks`에는 순서가 보존된 정확히 21개의 `{ "x", "y", "z" }` 객체가
들어간다. `timestamp`는 메시지 header stamp를 UTC RFC3339 형식으로
표현한 값이며, 같은 시각을 `stamp_sec`와 `stamp_nanosec`에도 기록한다.
`elapsed_ms`는 세션 시작 시각부터 메시지 stamp까지의 경과 시간(밀리초)이다.

`handedness`는 문자열이 아닌 `HandLandmarks`의 `uint8` 값이다.

| 값 | 의미 |
| --- | --- |
| 0 | UNKNOWN |
| 1 | LEFT |
| 2 | RIGHT |

LandMark JSON에는 `frame_id`를 공개하지 않는다.
