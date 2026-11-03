"""환경 변수 기반 설정. 로컬은 SQLite, Vercel은 외부 PostgreSQL."""
import hashlib
import secrets
import logging
import os
from datetime import timedelta, timezone

log = logging.getLogger("resume")


def _load_dotenv() -> None:
    """프로젝트 루트의 .env를 읽는다 (로컬 개발용). 이미 설정된 환경 변수는 덮어쓰지 않는다."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if key and value and key not in os.environ:
                os.environ[key] = value


_load_dotenv()

IS_VERCEL = bool(os.environ.get("VERCEL"))


def _database_url() -> str:
    # POSTGRES_URL: Vercel Marketplace의 Supabase 연동이 자동으로 넣어주는 pooler 주소
    url = (os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or "").strip()
    if not url:
        if IS_VERCEL:
            log.warning("DATABASE_URL 미설정: /tmp SQLite 사용 (재배포·콜드스타트 시 데이터 유실)")
            return "sqlite:////tmp/resume.db"
        return "sqlite:///./resume.db"
    # Supabase/Heroku 형식 정규화
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    if url.startswith("postgresql") and "?" in url:
        # libpq가 모르는 파라미터(예: supa=base-pooler.x)는 연결 오류를 내므로 제거
        base, query = url.split("?", 1)
        allowed = {"sslmode", "sslrootcert", "connect_timeout", "application_name", "options"}
        kept = [kv for kv in query.split("&") if kv.split("=", 1)[0] in allowed]
        url = base + ("?" + "&".join(kept) if kept else "")
    return url


DATABASE_URL = _database_url()


def _secret_key() -> str:
    key = os.environ.get("SECRET_KEY", "").strip()
    if key:
        return key
    if IS_VERCEL:
        if DATABASE_URL.startswith("sqlite"):
            # 공개된 값에서 키를 만들면 누구나 쿠키를 위조할 수 있으므로, 인스턴스마다 임의 키 사용
            log.warning("SECRET_KEY·DB 미설정: 임의 키 사용 (인스턴스가 바뀌면 다시 로그인해야 함)")
            return secrets.token_hex(32)
        # DB 주소에는 비밀번호가 들어 있어 외부에서 알 수 없다
        log.warning("SECRET_KEY 미설정: DB 주소에서 파생한 키 사용. SECRET_KEY를 설정하는 것을 권장합니다.")
        return hashlib.sha256(("resume-fallback:" + DATABASE_URL).encode()).hexdigest()
    return "dev-only-secret-key"


SECRET_KEY = _secret_key()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
# gemini-2.5-flash는 2026-11 기준 신규 사용자에게 제공 종료 (404)
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash").strip()
GEMINI_FALLBACK_MODELS = [
    m.strip() for m in os.environ.get("GEMINI_FALLBACK_MODELS", "gemini-3.8-flash,gemini-flash-latest").split(",") if m.strip()
]
CRON_SECRET = os.environ.get("CRON_SECRET", "").strip()

SESSION_COOKIE = "resume_session"
SESSION_DAYS = 30
LOCK_THRESHOLD = 5
LOCK_MINUTES = 5
DEMO_HOURS = 24
DEMO_PER_IP_PER_HOUR = 20
DEMO_TOTAL_PER_HOUR = 300
SIGNUP_PER_IP_PER_HOUR = 10
BRIEFING_PER_USER_PER_DAY = 30

# 한국은 서머타임이 없으므로 고정 오프셋 사용 (서버리스 환경의 tzdata 의존 제거)
KST = timezone(timedelta(hours=9))
