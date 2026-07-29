# send_motor_data.py
"""
thing_database_web의 업로드 API(/api/motor-data/upload/)로
가상의 모터 로그 데이터를 전송하는 테스트 스크립트입니다.

- 다운로드 센터(Vue의 DataDownloadView.vue)는 GET /api/motor-data/files/ 를 호출해
  DB(MotorLogFile)에 실제로 쌓인 데이터를 그대로 화면에 뿌려주는 구조입니다.
  즉, 이 스크립트로 업로드를 최소 1회 이상 성공시키면 다운로드 센터에 바로 표시됩니다.
- 기존 버전은 파일명(mock_motor_log.csv)이 항상 고정이라, 여러 번 실행해도
  로컬 media/ 폴더의 실제 파일은 계속 덮어써지고(DB row만 늘어남) "여러 건이 쌓인 모습"을
  확인하기 어려웠습니다. 이번 버전은 실행마다 고유한 파일명을 사용하고,
  --count 옵션으로 한 번에 여러 건을 만들어 다운로드 센터에서 바로 확인할 수 있게 했습니다.
- --url 옵션(또는 DJANGO_API_BASE_URL 환경변수)으로 로컬/EC2 서버 주소를 코드 수정 없이 바꿀 수 있습니다.
"""
import os
import csv
import random
import time
import argparse
import requests
from datetime import datetime

# 1. 설정 정보 -------------------------------------------------------------
# 로컬 개발 서버 : http://127.0.0.1:8000
# EC2 배포 서버  : http://<EC2 퍼블릭 IP 또는 도메인>:8000
#                 (nginx 등 리버스 프록시를 쓴다면 http://<도메인> 처럼 포트 없이)
# 환경변수 DJANGO_API_BASE_URL로 덮어쓸 수 있으므로, 코드를 고치지 않고도 --url 로 전환 가능
DEFAULT_BASE_URL = os.environ.get("DJANGO_API_BASE_URL", "http://127.0.0.1:8000")
UPLOAD_ENDPOINT = "/api/motor-data/upload/"

# 여러 로봇에서 데이터가 올라오는 상황을 흉내내기 위한 로봇 ID 목록
ROBOT_IDS = ["ROBOT_ARM_01", "ROBOT_ARM_02"]


def generate_mock_csv(file_path, robot_id, rows=20):
    """테스트용 가상 모터 로그 CSV 파일 생성 (시간, 모터각도, 전류량, 온도)

    rows: 한 번 업로드에 담을 로그 줄 수 (기본 20줄 — 실제 로그 파일처럼 보이도록 늘림)
    각도값은 매 줄 완전 랜덤이 아니라 이전 값 기준으로 조금씩 흔들리게 만들어서,
    실제 모터가 움직이는 듯한 그럴듯한 파형이 되도록 했습니다.
    """
    print(f"1. [{robot_id}] 가상 모터 데이터 파일 생성 중... ({rows}줄)")
    with open(file_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "motor_angle_deg", "current_mA", "temperature_c"])  # 헤더

        start_time = time.time()
        angle = random.uniform(0.0, 180.0)
        for i in range(rows):
            timestamp = datetime.fromtimestamp(start_time + i).isoformat(timespec="seconds")
            angle += random.uniform(-8.0, 8.0)
            angle = max(0.0, min(180.0, angle))
            current = round(random.uniform(100, 500), 1)
            temperature = round(random.uniform(28.0, 45.0), 1)
            writer.writerow([timestamp, round(angle, 2), current, temperature])
    print(f"   -> 파일 생성 완료: {file_path}")


def upload_to_django(base_url, file_path, robot_id, file_name):
    """생성된 CSV 파일을 Django 백엔드로 전송"""
    upload_url = base_url.rstrip("/") + UPLOAD_ENDPOINT
    print(f"2. [{robot_id}] Django 백엔드로 전송 시작 ({upload_url})...")

    with open(file_path, "rb") as f:
        files = {"file": (file_name, f, "text/csv")}
        data = {"robot_id": robot_id}

        try:
            response = requests.post(upload_url, data=data, files=files, timeout=10)

            if response.status_code == 201:
                print("   ✨ 업로드 성공! 다운로드 센터 목록에 새 항목이 추가되었습니다.")
                print(f"      서버 응답: {response.json()}")
                return True
            else:
                print(f"   ❌ 업로드 실패 (상태 코드: {response.status_code})")
                print(f"      서버 에러 메시지: {response.text}")
                return False

        except requests.exceptions.ConnectionError:
            print(
                f"   ❌ 에러: {base_url} 서버에 연결할 수 없습니다. "
                f"서버가 켜져 있는지, EC2라면 보안 그룹(인바운드 8000/TCP)이 열려 있는지 확인하세요."
            )
            return False
        except requests.exceptions.Timeout:
            print("   ❌ 에러: 서버 응답 시간 초과")
            return False


def run_once(base_url, robot_id, rows, keep_local_file=False):
    # 실행마다 고유한 파일명을 사용해, 서버 media/ 폴더에서 이전 파일을 덮어쓰지 않도록 함
    timestamp_tag = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    file_name = f"mock_motor_log_{robot_id}_{timestamp_tag}.csv"
    local_path = file_name

    generate_mock_csv(local_path, robot_id, rows=rows)
    ok = upload_to_django(base_url, local_path, robot_id, file_name)

    if not keep_local_file and os.path.exists(local_path):
        os.remove(local_path)
        print("3. 로컬 임시 파일 정리 완료.")

    return ok


def main():
    parser = argparse.ArgumentParser(
        description="thing_database_web 업로드 API로 가상 모터 로그를 전송하는 테스트 스크립트"
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_BASE_URL,
        help="Django 서버 주소 (기본값: %(default)s). EC2 예: http://<EC2 퍼블릭IP>:8000",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=3,
        help="업로드를 몇 번 반복할지 (기본 3회 — 다운로드 센터에 여러 건 쌓인 모습을 바로 확인 가능)",
    )
    parser.add_argument("--rows", type=int, default=20, help="한 CSV 파일에 담을 로그 줄 수")
    parser.add_argument(
        "--robot-id",
        default=None,
        help="특정 로봇 ID만 쓰고 싶을 때 지정 (기본은 ROBOT_ARM_01/02를 번갈아 사용)",
    )
    parser.add_argument(
        "--keep-local-file",
        action="store_true",
        help="업로드 후 로컬에 생성된 CSV 파일을 삭제하지 않고 보존",
    )
    args = parser.parse_args()

    print(f"대상 서버: {args.url}")
    success, fail = 0, 0
    for i in range(args.count):
        robot_id = args.robot_id or ROBOT_IDS[i % len(ROBOT_IDS)]
        print(f"\n=== [{i + 1}/{args.count}] {robot_id} 업로드 시작 ===")
        if run_once(args.url, robot_id, args.rows, args.keep_local_file):
            success += 1
        else:
            fail += 1
        if i < args.count - 1:
            time.sleep(0.5)  # created_at 값이 서로 겹치지 않도록 살짝 텀을 둠

    print(f"\n총 {args.count}건 중 성공 {success}건 / 실패 {fail}건")
    if success:
        print("👉 프론트엔드(Vue) 다운로드 센터(/download)에서 새로고침하면 방금 올린 데이터가 목록에 보입니다.")


if __name__ == "__main__":
    main()
