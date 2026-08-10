# backend/apps/limits.py
"""업로드 크기 상한 단일 출처.

Django 를 import 하지 않는다. `config/settings.py` 가 이 모듈을 읽기 때문이다.
순수 상수만 두고 어떤 부수효과도 만들지 않는다.

── 왜 한 곳에 모으는가 ────────────────────────────────────────────────────
landmark JSON 의 실제 용량이 아직 확정되지 않았다. 6.5절의 120MiB 는 "얼마나
커질지 모르니 일단" 붙여 둔 값이다. 그 값이 바뀌면 함께 움직여야 하는 것이
셋이다.

    apps.validators.PART_MAX_BYTES          part 별 검사
    config.settings.DATA_UPLOAD_MAX_MEMORY_SIZE   Django 요청 본문 상한
    deploy/nginx_...conf  client_max_body_size     Nginx 본문 상한

세 곳에 숫자를 흩어 두면 하나만 고치고 나머지를 잊는다. 그러면 큰 업로드가
Django 에 닿기 전에 Nginx 에서 413 으로 끊긴다. 원인을 찾기 어려운 실패다.

여기 값을 고치면 앞의 두 곳은 자동으로 따라온다. Nginx 는 설정 파일이라
import 할 수 없으므로 `apps/tests.py` 가 conf 파일을 읽어 대조한다.
숫자를 바꾸면 그 시험이 실패하면서 새로 넣을 값을 알려 준다.
"""
import math

KiB = 1024
MiB = 1024 * 1024

#: part 별 상한 (6.5절 / FR-51)
#: landmark 는 실제 용량 미확정. docs/pending-decisions.md P-2 참조.
PART_MAX_BYTES = {
    "metadata": 256 * KiB,
    "hand_command": 20 * MiB,
    "motor_status": 60 * MiB,
    "landmark": 120 * MiB,
}

#: 네 part 합계.
#: V7.1 §6.5 은 "네 part 합계 200.25MiB" (0.25 + 20 + 60 + 120, landmark 포함)다.
#: 숫자를 옮겨 적지 않고 실제 합을 쓴다.
TOTAL_MAX_BYTES = sum(PART_MAX_BYTES.values())

#: multipart 경계·헤더·필드가 차지하는 몫. 본문이 part 합계보다 조금 크다.
REQUEST_HEADROOM = 10 * MiB

#: 요청 본문 전체 상한. Django·Nginx 가 이 값을 쓴다.
REQUEST_MAX_BYTES = TOTAL_MAX_BYTES + REQUEST_HEADROOM


def nginx_client_max_body_size():
    """Nginx `client_max_body_size` 에 넣을 문자열. 올림해서 MiB 단위로."""
    return f"{math.ceil(REQUEST_MAX_BYTES / MiB)}M"
