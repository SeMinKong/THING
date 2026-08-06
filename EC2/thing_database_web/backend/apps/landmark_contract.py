# backend/apps/landmark_contract.py
"""LandMark JSON 계약 — 아직 확정되지 않은 부분을 한 곳에 모은다.

FR-49 는 세션마다 metadata JSON·HandCommand CSV·MotorStatus CSV·LandMark JSON
네 개를 공개하라고 한다. 그런데 **LandMark JSON 의 형식은 어디에도 정의되어
있지 않다.** 6.5절 metadata schema 의 landmark 항목은 이 상태다.

    "landmark": {
      "filename": "session_..._landmark.json",
      "size_bytes": 12345,
      "json_data": 1234,          <- row_count 자리에 있으나 의미 불명
      "sha256": "UTF-8"           <- 값이 hex 가 아니고 주석에 "수정 필요"
    }

그래서 지금은 **"올바른 JSON 이면 무엇이든 받는다"** 로 열어 두고, 형식이
정해지면 이 파일 한 곳만 고치면 되도록 만들었다. 다른 모듈은 여기서만 읽는다.

── 형식이 정해지면 할 일 ──────────────────────────────────────────────────
1. SCHEMA_DECIDED 를 True 로 바꾼다.
2. ROOT_TYPE 를 실제 최상위 타입으로 정한다 (dict 또는 list).
3. REQUIRED_KEYS 에 필수 키를 적는다 (ROOT_TYPE 이 dict 일 때만).
4. 더 세밀한 검사가 필요하면 validate_payload() 안의 표시된 자리에 넣는다.
그 외 파일은 손대지 않아도 된다.
"""
import json

from . import limits
from .errors import ValidationFailed

# ── 파일 계약 ──────────────────────────────────────────────────────────────

#: metadata.files 와 다운로드 경로에서 쓰는 종류 이름
KIND = "landmark"

#: 확장자와 content type
EXTENSION = "json"
CONTENT_TYPES = ("application/json",)

#: part 상한. apps/limits.py 가 단일 출처다.
#: 6.5절의 120MiB 는 실제 용량을 몰라 붙여 둔 임시값이므로 바뀔 것이고, 바뀌면
#: Django·Nginx 상한이 함께 움직여야 한다. docs/pending-decisions.md P-2 참조.
MAX_BYTES = limits.PART_MAX_BYTES[KIND]

#: 이 part 를 반드시 받을 것인가.
#:
#: 공개 파일은 네 개로 확정됐다 (FR-49 / 6.5절 / 8.3절 검수 6).
#: 그런데 업로더(Raspberry Pi)가 네 part 를 보내기 시작했는지는 별개 문제다.
#: True 로 바꾼 순간 세 part 업로드는 전부 400 이 되고, 로봇에는 미전송 queue 가
#: 없으므로(7.3절) 그 세션은 사라진다. 업로더 전환을 확인한 뒤에 바꾼다.
#: docs/pending-decisions.md C-1 참조.
REQUIRED = False

#: content_digest 계산에 이 파일의 files 항목을 포함할 것인가.
#: 로봇 exporter(thing_logger)의 calculate_content_digest 는 content_digest 만 빼고
#: files 전체(landmark 포함)로 digest 를 계산한다. 서버가 landmark 를 빼고 재계산하면
#: 로봇 digest 와 어긋나 모든 업로드가 422 가 되므로 포함으로 맞춘다.
#: 로봇은 항상 네 파일을 보내므로(build_metadata) landmark 유무로 같은 세션이 흔들리지
#: 않아 멱등성(NFR-26)도 안전하다.
#: 주의: V7.1 §6.5 본문은 여전히 "두 CSV" 만 열거한다 — 스펙 문구 정정 대상.
#: docs/pending-decisions.md P-2 (결정: 로봇을 따른다).
INCLUDE_IN_DIGEST = True

# ── 내용 검증 ──────────────────────────────────────────────────────────────

#: 형식이 정해졌는가. False 인 동안에는 "올바른 JSON" 까지만 확인한다.
SCHEMA_DECIDED = False

#: 최상위 타입. SCHEMA_DECIDED 가 True 일 때만 검사한다.
ROOT_TYPE = dict

#: 최상위 필수 키. ROOT_TYPE 이 dict 이고 SCHEMA_DECIDED 가 True 일 때만 검사한다.
REQUIRED_KEYS = ()


def validate_payload(raw_bytes):
    """LandMark JSON 본문을 검증하고 파싱 결과를 돌려준다.

    형식이 미정이므로 지금은 두 가지만 본다.
      - UTF-8 로 디코딩되는가
      - 올바른 JSON 인가
    이것만으로도 잘못된 part(CSV·바이너리·잘린 파일)를 걸러낸다.

    반환값은 (parsed, item_count) 다. item_count 는 metadata 의 개수 필드와
    맞춰 보기 위한 값이며, 형식이 정해지기 전에는 최상위 컨테이너 길이다.
    """
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise ValidationFailed(details=[f"{KIND}: UTF-8 로 디코딩되지 않는다"])

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationFailed(
            details=[f"{KIND}: 올바른 JSON 이 아니다 (line {exc.lineno} col {exc.colno})"]
        )

    if SCHEMA_DECIDED:
        if not isinstance(parsed, ROOT_TYPE):
            raise ValidationFailed(
                details=[f"{KIND}: 최상위가 {ROOT_TYPE.__name__} 이어야 한다"]
            )
        if ROOT_TYPE is dict:
            missing = [k for k in REQUIRED_KEYS if k not in parsed]
            if missing:
                raise ValidationFailed(
                    details=[f"{KIND}: 필수 키 누락 {', '.join(missing)}"]
                )
        # ── 형식이 정해지면 추가 검사를 여기에 넣는다 ──

    return parsed, _item_count(parsed)


def _item_count(parsed):
    """개수 세기. 형식 미정이라 최상위 컨테이너 길이로 둔다."""
    if isinstance(parsed, (list, dict)):
        return len(parsed)
    return 0


def sample_payload(frame_count=20, hz=20):
    """시험용 LandMark JSON 을 만든다.

    형식이 미정이므로 **이 모양에 아무 근거가 없다.** 다만 생성기와 검증기가
    서로 다른 모양을 가정하면 시험이 의미를 잃으므로, 모양을 정하는 곳을
    이 파일 하나로 묶어 둔다. 형식이 정해지면 여기와 validate_payload() 를
    함께 고친다.

    HandLandmarks.msg 의 landmarks[21] 을 프레임마다 담는 형태로 두었다.
    21 랜드마크 · x,y,z 는 실제 계약이므로 그 부분만은 맞다.
    """
    import math

    frames = []
    for i in range(frame_count):
        phase = i / max(hz, 1)
        frames.append({
            "elapsed_ms": int(i * 1000 / max(hz, 1)),
            "detected": True,
            "confidence": round(0.90 + 0.05 * math.sin(phase), 3),
            # MediaPipe 21 랜드마크. 0 손목, 1-4 엄지, 5-8 검지, 9-12 중지,
            # 13-16 약지, 17-20 소지 (HandLandmarks.msg 와 같은 순서)
            "landmarks": [
                {
                    "x": round(0.5 + 0.10 * math.sin(phase + k * 0.3), 4),
                    "y": round(0.5 + 0.10 * math.cos(phase + k * 0.3), 4),
                    "z": round(0.02 * math.sin(phase + k), 4),
                }
                for k in range(21)
            ],
        })
    return {"schema_note": "형식 미정 (docs/pending-decisions.md P-1)", "frames": frames}


def describe_state():
    """지금 무엇을 가정하고 도는지. 진단·문서에서 읽는다."""
    return {
        "형식 확정": SCHEMA_DECIDED,
        "필수 part": REQUIRED,
        "digest 포함": INCLUDE_IN_DIGEST,
        "상한": f"{MAX_BYTES // (1024 * 1024)}MiB",
        "검사 범위": "UTF-8 + JSON 파싱" if not SCHEMA_DECIDED else "스키마 검사",
    }
