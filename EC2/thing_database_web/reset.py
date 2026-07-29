# robot-web-project/reset_db.py
import os
import shutil
import subprocess
import sys

# 1. 절대 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(BASE_DIR, 'backend')
DB_PATH = os.path.join(BACKEND_DIR, 'db.sqlite3')
MEDIA_DIR = os.path.join(BACKEND_DIR, 'media')
MIGRATIONS_DIR = os.path.join(BACKEND_DIR, 'apps', 'migrations')

print("🧹 Django 로컬 DB 및 테스트 파일 데이터 초기화를 시작합니다...")

# 2. 업로드된 미디어 폴더(CSV 파일들) 삭제
if os.path.exists(MEDIA_DIR):
    shutil.rmtree(MEDIA_DIR)
    print("   -> [완료] 업로드된 미디어 파일 폴더(media/)를 삭제했습니다.")

# 3. 데이터베이스 파일(db.sqlite3) 삭제
if os.path.exists(DB_PATH):
    try:
        os.remove(DB_PATH)
        print("   -> [완료] 기존 데이터베이스 파일(db.sqlite3)을 삭제했습니다.")
    except PermissionError:
        print("❌ 에러: Django 서버(runserver)가 켜져 있으면 DB 파일을 완전히 지울 수 없습니다!")
        print("         터미널에서 구동 중인 Django 서버를 완전히 종료(Ctrl + C)한 뒤 다시 실행하세요.")
        sys.exit(1)

# 4. 앱 내의 마이그레이션(DB 설계도) 기록 초기화 (__init__.py 제외)
if os.path.exists(MIGRATIONS_DIR):
    for filename in os.listdir(MIGRATIONS_DIR):
        file_path = os.path.join(MIGRATIONS_DIR, filename)
        if filename != '__init__.py' and not filename.startswith('__pycache__'):
            if os.path.isfile(file_path):
                os.remove(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
    print("   -> [완료] 구버전 DB 설계도(migrations) 기록을 지웠습니다.")

print("\n🚀 깨끗한 상태로 데이터베이스 재구축을 시작합니다...")

ENV_NAME = "ec2_web"


def resolve_python_command():
    """현재 활성화된 Conda/Miniforge 가상환경을 기준으로 Django 실행 명령을 반환합니다."""
    conda_prefix = os.environ.get("CONDA_PREFIX")
    conda_default_env = os.environ.get("CONDA_DEFAULT_ENV", "")

    if conda_prefix:
        for candidate in [
            os.path.join(conda_prefix, "python.exe"),
            os.path.join(conda_prefix, "python"),
        ]:
            if os.path.exists(candidate):
                return [candidate]

    if conda_default_env.lower() == ENV_NAME.lower() and conda_prefix:
        for candidate in [
            os.path.join(conda_prefix, "python.exe"),
            os.path.join(conda_prefix, "python"),
        ]:
            if os.path.exists(candidate):
                return [candidate]

    conda_exe = os.environ.get("CONDA_EXE")
    if conda_exe and os.path.exists(conda_exe):
        return [conda_exe, "run", "-n", ENV_NAME, "python"]

    if os.name == "nt":
        home_dir = os.path.expanduser("~")
        for base_dir in [
            os.path.join(home_dir, "miniforge3"),
            os.path.join(home_dir, "mambaforge"),
            os.path.join(home_dir, "miniconda3"),
            os.path.join(home_dir, "anaconda3"),
        ]:
            conda_candidate = os.path.join(base_dir, "Scripts", "conda.exe")
            if os.path.exists(conda_candidate):
                return [conda_candidate, "run", "-n", ENV_NAME, "python"]

    if "VIRTUAL_ENV" in os.environ:
        venv_python = os.path.join(os.environ["VIRTUAL_ENV"], "Scripts", "python.exe")
        if os.path.exists(venv_python):
            return [venv_python]

    return [sys.executable]


python_command = resolve_python_command()
print(f"🎯 실행 대상: {' '.join(python_command)}")

required_packages = [
    "django",
    "djangorestframework",
    "django-cors-headers",
]

try:
    # 필요한 패키지가 빠져 있으면 가상환경에 설치합니다.
    subprocess.run(python_command + ["-m", "pip", "install", "--upgrade", "pip"], cwd=BACKEND_DIR, check=True)
    subprocess.run(python_command + ["-m", "pip", "install", *required_packages], cwd=BACKEND_DIR, check=True)

    # 5. 새 설계도 생성 (makemigrations apps)
    subprocess.run(python_command + ["manage.py", "makemigrations", "apps"], cwd=BACKEND_DIR, check=True)

    # 6. 설계도 내용 빈 DB에 이식 (migrate)
    subprocess.run(python_command + ["manage.py", "migrate"], cwd=BACKEND_DIR, check=True)

    print("\n✨ 모든 초기화 과정이 끝났습니다! 완전히 깨끗한 새 데이터베이스가 생성되었습니다.")

except subprocess.CalledProcessError as e:
    print(f"\n❌ Django 명령 실행 중 에러가 발생했습니다.")
    print(f"상세 에러 내용: {e}")
