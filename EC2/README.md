# EC2 Portal

팀원이 전달한 EC2 웹 작업의 단일 원본은
[`thing_database_web/`](thing_database_web/)입니다.

기존 frontend, backend, deploy, `reset.py`, `test.py` 구조를 유지합니다.
팀원은 EC2 관련 작업을 이 경로에서 계속 진행하고 별도
`web/ec2-portal` 복제본을 만들지 않습니다.

현재 전달본은 진행 중인 프로토타입입니다. 폴더를 저장소에 추가하는 것은
V6.3의 인증·세 파일·READY·HTTPS 계약이 모두 구현됐다는 의미가 아닙니다.
최종 계약은 `docs/requirements/요구사항 명세서 V6.3.md`를 따릅니다.

실제 `.env`, SQLite DB, media 파일, production 환경 파일과 개인 IDE 설정은
로컬에 유지하되 Git에는 포함하지 않습니다.
