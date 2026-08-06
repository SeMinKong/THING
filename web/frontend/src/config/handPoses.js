// ============================================================================
// 조작 모드 자세 데이터 — 명령별 손 자세(21 MediaPipe 랜드마크)
// ----------------------------------------------------------------------------
// 왜 하드코딩인가
//   조작 모드의 손은 사람이 연속으로 움직이는 게 아니라 정해진 명령 자세만
//   취한다. 그래서 라이브 모터값을 역산(21↔7 은 비가역)하지 않고, 각 명령이
//   어떤 자세인지 미리 정한 개략도로 보여 준다.
//
// 좌표계
//   GesturePreview 가 쓰던 것과 동일한 viewBox 0 0 200 260, 손바닥 정면 2D.
//   0 손목, 1-4 엄지, 5-8 검지, 9-12 중지, 13-16 약지, 17-20 소지.
//
// 파생 방식
//   편 손(OPEN)과 주먹(FIST) 두 완결 자세만 손으로 찍고, 나머지 자세는
//   "굽힐 손가락은 FIST 좌표, 펼 손가락은 OPEN 좌표"로 손가락 단위로 갈아끼워
//   만든다. 두 원본이 모두 해부학적으로 완결이라 손가락을 섞어도 어긋나지 않고,
//   21점을 자세마다 직접 타이핑할 때 생기는 실수를 없앤다.
//
// 정확도
//   자세·스텝 수·순서는 화면 설명용 개략도다. 실제 choreography 와 7논리축
//   목표값은 로봇(FR-41, YAML) 기준이며 이 화면은 로봇에 아무것도 보내지 않는다.
// ============================================================================
import { CANONICAL_GESTURES } from "./messageProtocol";

// GesturePreview 의 POSES 에서 그대로 가져온 두 원본 자세
const OPEN = [
  [100, 232], [74, 208], [50, 184], [32, 164], [18, 146],
  [78, 158], [72, 122], [68, 98], [64, 76],
  [101, 150], [100, 110], [99, 84], [98, 60],
  [124, 156], [129, 118], [132, 94], [134, 72],
  [146, 170], [156, 140], [161, 120], [165, 102],
];
const FIST = [
  [100, 232], [74, 208], [56, 190], [58, 170], [74, 164],
  [78, 158], [76, 130], [88, 122], [92, 140],
  [101, 150], [101, 122], [111, 116], [109, 136],
  [124, 156], [126, 128], [134, 124], [130, 144],
  [146, 170], [150, 146], [156, 144], [150, 160],
];
// 파지 두 종은 사람이 흉내내기 어려워 별도 원본으로 둔다
const PINCH = [
  [100, 232], [74, 208], [54, 186], [52, 158], [64, 132],
  [78, 158], [72, 124], [68, 108], [68, 126],
  [101, 150], [100, 116], [104, 94], [104, 74],
  [124, 156], [129, 120], [132, 98], [134, 78],
  [146, 170], [154, 140], [158, 120], [162, 104],
];
const CYLINDRICAL = [
  [100, 232], [74, 208], [52, 188], [46, 164], [60, 150],
  [78, 158], [70, 128], [66, 108], [78, 114],
  [101, 150], [99, 118], [97, 98], [106, 106],
  [124, 156], [128, 122], [130, 102], [121, 110],
  [146, 170], [152, 138], [154, 120], [145, 128],
];

// 손가락별 랜드마크 index (손목 0 은 어느 손가락에도 안 들어가 항상 고정)
const FINGER = {
  thumb: [1, 2, 3, 4],
  index: [5, 6, 7, 8],
  middle: [9, 10, 11, 12],
  ring: [13, 14, 15, 16],
  pinky: [17, 18, 19, 20],
};

/** 지정한 손가락만 주먹처럼 굽히고 나머지는 편 손 그대로 둔 자세를 만든다 */
function curl(curledFingers) {
  const curled = new Set(curledFingers.flatMap((f) => FINGER[f]));
  return OPEN.map((point, i) => (curled.has(i) ? FIST[i] : point));
}

// 가위바위보: 보=편 손, 바위=주먹, 가위=검지·중지만 편다
const SCISSORS = curl(["thumb", "ring", "pinky"]);

// 카운트다운 5→1: 편 손가락 수가 곧 숫자. 스텝마다 하나씩 더 접는다
const COUNT_5 = OPEN;                                    // 다섯 손가락
const COUNT_4 = curl(["thumb"]);                         // 엄지 접음
const COUNT_3 = curl(["thumb", "pinky"]);                // 엄지·소지 접음
const COUNT_2 = curl(["thumb", "ring", "pinky"]);        // 검지·중지 (= 가위)
const COUNT_1 = curl(["thumb", "middle", "ring", "pinky"]); // 검지만

/** 단일 제스처 명령 → 자세. commandPresets 의 BASIC_GESTURES id 와 맞춘다 */
export const GESTURE_POSES = {
  open: { name: "편 손", pts: OPEN },
  fist: { name: "주먹", pts: FIST },
  pinch: { name: "집기", pts: PINCH },
  cylindrical_grasp: { name: "원통 파지", pts: CYLINDRICAL },
};

/** 시퀀스 명령 → 스텝 자세 배열. commandPresets 의 SEQUENCE_ACTIONS id 와 맞춘다 */
export const SEQUENCE_POSES = {
  scissors_rock_paper: [
    { name: "가위", pts: SCISSORS },
    { name: "바위", pts: FIST },
    { name: "보", pts: OPEN },
  ],
  countdown: [
    { name: "5", pts: COUNT_5 },
    { name: "4", pts: COUNT_4 },
    { name: "3", pts: COUNT_3 },
    { name: "2", pts: COUNT_2 },
    { name: "1", pts: COUNT_1 },
  ],
};

/**
 * 명령이 보여 줄 자세 스텝 배열을 돌려준다.
 * 제스처는 길이 1, 시퀀스는 여러 개. 해당 자세가 없으면 null.
 */
export function poseSteps(command) {
  if (!command) return null;
  if (SEQUENCE_POSES[command.id]) return SEQUENCE_POSES[command.id];
  const gesture = GESTURE_POSES[command.id];
  return gesture ? [gesture] : null;
}

// 화면 이름과 계약 이름이 어긋나면 조용히 실패한다. 개발 중에 잡는다.
if (import.meta.env?.DEV) {
  const unknown = Object.keys(GESTURE_POSES).filter((id) => !CANONICAL_GESTURES.includes(id));
  if (unknown.length > 0) {
    console.warn("[handPoses] canonical 이 아닌 gesture:", unknown);
  }
}
