# Vision experiments

이 디렉터리는 사용자 캘리브레이션 자료와 ROS 2에 넣기 전의 제한된 비전 실험을
관리합니다. 실제 실행 노드와 공용 알고리즘은
`thing_ws/src/thing_vision`에서 구현합니다.

- `calibration/`: 사용자별 편 손·주먹 기준과 샘플
- `experiments/`: 필요할 때 생성하는 일회성 MediaPipe·기하 계산 실험
- `requirements.txt`: ROS 비전 노드와 실험에서 공통으로 사용하는 Python 의존성

프로덕션 코드가 이 디렉터리와 `thing_vision`에 중복되지 않도록 합니다.
