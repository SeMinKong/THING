// ============================================================================
// FR-22 조작 모드 명령 프리셋
// ----------------------------------------------------------------------------
// 7축 목표값(values)은 이 파일에서 제거했다.
// ExecuteGesture.srv 의 요청 필드는 gesture_name·speed_limit 뿐이고 FR-41 이
// "YAML에서 ... gesture 목표를 관리한다" 고 했다. 웹이 목표값을 함께 들고 있으면
// YAML 과 이중 관리가 되어, 화면이 보여주는 자세와 실제 자세가 갈려도 알 수 없다.
// speed_limit 은 .srv 가 호출자에게 요구하는 필드라 남긴다.
//
// (이전 주석) 실제 자세별 목표값은
// 기구(텐던/스풀) 튜닝 결과에 맞춰 이 파일만 수정하면 되도록 분리해두었다.
//
// 주의: 최종적으로는 이 목표값들이 Raspberry Pi 쪽 thing_control 패키지의
// Gesture 서비스에도 동일하게 정의되어야 한다(UC-06, FR-29). 웹은 참고용
// 목표값을 함께 보내되, 실제 안전 범위 검증과 최종 실행 여부는 항상
// command_guard/safety_manager가 최종 판단한다 (웹은 안전 판단 주체가 아님).
// ============================================================================
import { HAND_SOURCE } from "./messageProtocol";

// FR-22 기본 명령: 사전 정의 자세(Gesture) - 값이 정해져 있는 단발성 자세.

// FR-38 Must canonical gesture: open, fist, pinch, cylindrical_grasp
// alias: home|paper -> open, rock -> fist (중복 7축 값을 만들지 않는다)
// 이름이 한 글자만 달라도 /thing/execute_gesture 호출이 실패한다.
export const BASIC_GESTURES = [
  {
    id: "open",
    icon: "✋",
    label: "손 펴기",
    source: HAND_SOURCE.GESTURE,
    speed_limit: 1.0, // 추가: FR-06, FR-32 속도 제한값
  },
  {
    id: "fist",
    icon: "👊",
    label: "주먹 쥐기",
    source: HAND_SOURCE.GESTURE,
    speed_limit: 1.0,
  },
  {
    id: "cylindrical_grasp",
    icon: "🤝",
    label: "원통 파지",
    source: HAND_SOURCE.GESTURE,
    speed_limit: 0.5,
  },
  {
    id: "pinch",
    icon: "🤏",
    label: "집기 (엄지-검지)",
    source: HAND_SOURCE.GESTURE,
    speed_limit: 0.5,
  },
];

// 제거된 프리셋: INIT_POSE_GESTURE (id "init_pose")
//
// FR-38 이 canonical gesture 를 open·fist·pinch·cylindrical_grasp 로, alias 를
// home|paper→open, rock→fist 로 동결했다. "init_pose" 는 둘 중 어디에도 없어
// consumers.canonical_gesture() 가 None 을 돌려주고 서버가 web_malformed_request
// 로 거부한다. 즉 화면에는 보이지만 누르면 오류만 뜨는 버튼이었다.
// 7축 값이 open 과 완전히 동일했으므로 FR-22 의 "손 펴기" 버튼으로 대체된다.
// 별도 초기 자세 버튼은 FR-22 에서 Could 이므로 2주 범위 밖이며, 되살리려면
// GESTURE_ALIASES 에 init_pose→open 을 추가하는 결정을 docs/interfaces.md 에
// 먼저 반영해야 한다.


// FR-22 추가 명령: 단일 자세가 아니라 시간에 따라 여러 자세를 거치는
// 연속 동작이므로 목표값을 웹이 들고 있지 않고, ROS2 Sequence 액션의
// 식별자(sequence_id)만 전달한다. 실제 동작 시퀀스는 thing_control
// 패키지(Raspberry Pi)에서 관리한다 (1.3, UC-06 "정해진 동작을 명령 가능한
// service/action 실행").
export const SEQUENCE_ACTIONS = [
  // speed_limit 은 ExecuteSequence.action 의 goal 필드다 (FR-32: 0.0 초과 1.0 이하).
  { id: "scissors_rock_paper", icon: "✂️", label: "가위바위보", speed_limit: 0.5 },
  { id: "countdown", icon: "⏱️", label: "5부터 카운트다운", speed_limit: 0.5 },
];
