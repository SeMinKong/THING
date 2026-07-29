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
        'NAME': BASE_DIR / 'db.sqlite3',
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
CORS_ALLOW_CREDENTIALS = True
SESSION_COOKIE_SAMESITE = 'Lax'

# Django admin(/admin/) 등 세션+CSRF 폼 로그인 시 배포 도메인에서 접속을 허용하려면
# .env에 CSRF_TRUSTED_ORIGINS=http://i15c103.p.ssafy.io 형태로 채워야 함 (스킴 필수)
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[])


# 11. 로컬 가상 미디어 파일 저장 경로 (AWS S3 연동 전에 파일 다운로드 테스트용)
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')


# 12. 실배포용 AWS S3 설정 뼈대 (향후 IAM 키 발급 후 가동 가능)
AWS_ACCESS_KEY_ID = env('AWS_ACCESS_KEY_ID', default='YOUR_ACCESS_KEY')
AWS_SECRET_ACCESS_KEY = env('AWS_SECRET_ACCESS_KEY', default='YOUR_SECRET_KEY')
AWS_STORAGE_BUCKET_NAME = env('AWS_STORAGE_BUCKET_NAME', default='your-robot-data-bucket')
AWS_S3_REGION_NAME = env('AWS_S3_REGION_NAME', default='ap-northeast-2')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
