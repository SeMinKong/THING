# 데이터 기록 시험 절차

1. MIMIC 모드에서만 녹화 시작이 허용되는지 확인합니다.
2. 시작 응답 Session ID와 `RecordingState.active_session_id`가 일치하는지 확인합니다.
3. 녹화 중 모든 일반 모드 변경 요청이 거부되는지 확인합니다.
4. rosbag2에 HandLandmarks, HandCommand와 MotorStatus가 포함되는지 확인합니다.
5. 카메라 영상 파일이 생성되지 않는지 확인합니다.
6. 종료 후 `active_session_id=0`과 최근 세션 정보가 발행되는지 확인합니다.
7. 성공·실패 판정 전 다음 녹화가 거부되는지 확인합니다.
8. 판정 후 다음 세션을 시작할 수 있는지 확인합니다.
9. rosbag2를 재생해 timestamp와 메시지 필드를 분석할 수 있는지 확인합니다.
