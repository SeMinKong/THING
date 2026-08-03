import os
from pathlib import Path
import environ

# 1. 경로 설정 (프로젝트 루트: backend/)
BASE_DIR = Path(__file__).resolve().parent.parent

# 2. 환경변수(django-environ) 설정 초기화 및 로드
env = environ.Env(
    DEBUG=(bool, False) # 기본값은 안전하게 False로 지정
)
# backend/config/.env 파일을 읽어옵니다.
environ.Env.read_env(os.path.join(BASE_DIR, 'config', '.env'))

# 3. 환경변수 파일(.env) 기반 핵심 보안 변수 매핑
SECRET_KEY = env('SECRET_KEY', default='django-insecure-default-local-key-change-this-in-prod')
DEBUG = env.bool('DEBUG', default=True)
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1'])


# 4. 애플리케이션 및 라이브러리 등록
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # 설치한 외부 패키지 및 단일 메인 앱
    'corsheaders',      # CORS 차단 해결용 미들웨어 라이브러리
    'rest_framework',   # Django REST Framework
    'apps',             # 가입/로그인/모터 로그 관리를 처리하는 단일 앱
]


# 5. 미들웨어 파이프라인 (CorsMiddleware는 반드시 최상단 CommonMiddleware 위에 배치)
MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# 6. 데이터베이스 설정 (로컬 개발용 가벼운 SQLite 파일 구조)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        # [FR-52 / NFR-29] 명세서 규격 경로는 /var/lib/thing-data/db.sqlite3 다.
        # 기본값은 현재 위치를 유지한다. 기존 DB 를 복사한 뒤 .env 로 전환한다.
        'NAME': env('DB_PATH', default=str(BASE_DIR / 'db.sqlite3')),
    }
}


# 7. 패스워드 검증 구조 (유저 가입 시 동작)
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# 8. 국가 및 표준 시간대 설정 (대한민국 표준시 정렬)
LANGUAGE_CODE = 'ko-kr'
TIME_ZONE = 'Asia/Seoul'
USE_I18N = True
USE_TZ = True


# 9. 정적 파일 (CSS, JS) 처리 설정
STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')


# 10. CORS (교차 출처 자원 공유) 및 세션 공유 설정
# 로컬 개발 환경(Vite 기본 포트) + .env의 CORS_EXTRA_ORIGINS로 배포 도메인 추가 가능
CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
] + env.list('CORS_EXTRA_ORIGINS', default=[])

# 프론트와 백엔드가 서로 다른 포트/도메인 간에 쿠키(세션)를 주고받을 수 있도록 허용
CORS_ALLOW_CREDENTIALS = False
SESSION_COOKIE_SAMESITE = 'Lax'

# Django admin(/admin/) 등 세션+CSRF 폼 로그인 시 배포 도메인에서 접속을 허용하려면
# .env에 CSRF_TRUSTED_ORIGINS=http://i15c103.p.ssafy.io 형태로 채워야 함 (스킴 필수)
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[])



# 11-3. [FR-51] 장치 token
# 형식: <robot_id>:<sha256hex>[,<robot_id>:<sha256hex>]
# 평문 token 은 로봇에만 두고 서버에는 hash 만 보관한다.
DEVICE_TOKENS = env('DEVICE_TOKENS', default='')

REST_FRAMEWORK = {
    'EXCEPTION_HANDLER': 'apps.errors.api_exception_handler',
    'DEFAULT_THROTTLE_RATES': {
        'public': '120/min',   # 공개 GET, IP 당
        'upload': '10/min',    # 업로드 POST, token 당
    },
    'UNAUTHENTICATED_USER': None,
}


# 11-5. [6.5절] rate limit 카운터 공유용 캐시
# gunicorn 워커가 3개라 LocMemCache 를 쓰면 워커마다 독립 카운터가 생겨
# 실효 한도가 약 3배가 된다. 명세서가 숫자를 명시했으므로 DB 캐시로 공유한다.
# 배포 시 `python manage.py createcachetable` 을 한 번 실행한다.
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
        'LOCATION': 'thing_cache_table',
        'TIMEOUT': 300,
        'OPTIONS': {'MAX_ENTRIES': 10000},
    }
}


# 11-2. [FR-52 / NFR-29] 세션 데이터 루트
# 명세서 6.5절: SQLite는 /var/lib/thing-data/db.sqlite3,
#              파일은 /var/lib/thing-data/sessions/{robot_id}/{session_id}/
# systemd 의 StateDirectory=thing-data 가 이 경로를 생성·소유한다.
EC2_DATA_DIR = env('EC2_DATA_DIR', default='/var/lib/thing-data')


# 11-1. [B-3 수정] 업로드 요청 크기 상한
# 명세서 6.5절: metadata 256KiB + hand_command 20MiB + motor_status 60MiB
#              = 합계 80.25MiB, Django 요청 85MiB, Nginx body 90MiB로 고정.
# 이전에는 상한이 어디에도 설정되어 있지 않아 Nginx 기본값(1MB)에서 413으로 막혔다.
MiB = 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 85 * MiB   # 요청 본문 전체 상한
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * MiB    # 이 크기를 넘으면 메모리 대신 임시파일로 스풀
DATA_UPLOAD_MAX_NUMBER_FIELDS = 100



DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
# 배포(HTTPS) 전용 보안 설정
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False
    SILENCED_SYSTEM_CHECKS = ['security.W005', 'security.W021']