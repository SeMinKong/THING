# Vision

이 디렉터리는 `src/`의 손 인식·동작 매핑 코드와 `calibration/`의 사용자별 보정 데이터를 관리합니다.

카메라 입력, 손 landmark 검출, 사용자 캘리브레이션, 손가락 굽힘 계산 및 시리얼 전송을 구현합니다.

- `src/`: 비전 및 통신 소스
- `calibration/`: 캘리브레이션 설정과 샘플

Python 의존성 버전은 최초 실행 환경 검증 후 `requirements.txt`에 고정합니다.
