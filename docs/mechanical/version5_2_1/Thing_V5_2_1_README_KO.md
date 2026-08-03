# Thing V5.2.1 Closed-Guide

V5.2 원본을 보존하면서 손가락 링크 숫자 각인과 폐쇄형 힘줄 가이드를 적용한 기계 설계 개정판이다.

## 적용 내용

- 엄지 `0`, 검지 `1`, 중지 `2`, 약지 `3`, 소지 `4`를 각 손가락의 3개 분리 링크에 오목 각인: 총 15개.
- 폐쇄 기능 통로 35개: 일반 `Ø2.40 mm` 통로 32개와 CMC-B 특수 통로 3개.
- `A5` 관절 인접 통로는 외경 `Ø4.20 mm`로 축소.
- `LITTLE_A5`는 DIP 간섭을 피하는 `Ø4.20 × 1.60 mm` 폐쇄 포획 링.
- 엄지 MCP Bowden 폐쇄 링은 힘줄 축을 유지한 채 중수골 방향으로 `2.00 mm` 이동해 직접 융합.
- V5.2의 관절축, ROM, 핀, M2/M3, CMC 체결부와 특수 스웨이지·PTFE 단차 유지.

## 파일

- `Thing_V5_2_1_Closed_Guide.blend`: 편집 가능한 최종 Blender 모델.
- `Thing_V5_2_1_Closed_Guide_STL/`: 개별 STL 25개와 전체 배치 STL 1개.
- `Thing_V5_2_1_Closed_Guide_Audit.json`: 형상·벽 두께·각인·동작 스윕 감사 결과.
- `Thing_V5_2_1_STL_Strict_Validation.json`: 독립 STL 수밀성·바디·체적 검사 결과.
- `Thing_V5_2_1_Assembly_Addendum_KO.md`: 조립 순서와 출력·실물 검증 주의사항.
- `Thing_V5_2_1_Previews/`: 전체 및 상세 시각 검사 이미지.

## 검증 상태

- Blender 내부 감사: PASS.
- 개별 STL 25개: 각각 단일 수밀 바디 PASS.
- 합본 STL: 25개 바디 PASS.
- 손가락 16자세, 엄지 굽힘 4자세, CMC 90개 조합: 새 충돌 0.

실제 프린터·재료에서 `Ø2.40 mm` 보어 쿠폰, PTFE 삽입, 인장 및 반복 굴곡 시험은 별도로 수행해야 한다.
