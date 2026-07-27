# Launch files

장치별 실행 노드가 구현되면 다음 launch 파일을 이 디렉터리에 추가합니다.

- `jetson.launch.py`: camera, MediaPipe, target, MJPEG, Web Bridge
- `raspberry_pi.launch.py`: command manager/guard, safety, DYNAMIXEL
- `integration.launch.py`: 개발용 통합 실행

존재하지 않는 실행 파일을 참조하는 가짜 launch는 추가하지 않습니다. 각 launch는
관련 노드의 Jira 완료 조건과 실행 검증이 함께 준비된 MR에서 추가합니다.
