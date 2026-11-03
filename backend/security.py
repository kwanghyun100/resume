"""PIN 해시와 세션 쿠키 서명. 외부 의존성 없이 표준 라이브러리만 사용."""
import base64
import hashlib
import hmac
import secrets
import time

from .config import SECRET_KEY, SESSION_DAYS

_PBKDF2_ITER = 120_000


def hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, _PBKDF2_ITER)
    return "pbkdf2_sha256${}${}${}".format(
        _PBKDF2_ITER, base64.b64encode(salt).decode(), base64.b64encode(digest).decode()
    )


def verify_pin(pin: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, digest_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), base64.b64decode(salt_b64), int(iters))
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except Exception:
        return False


def _sign(payload: str) -> str:
    return hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_session_token(user_id: int) -> str:
    expires = int(time.time()) + SESSION_DAYS * 86400
    payload = f"{user_id}.{expires}"
    return f"{payload}.{_sign(payload)}"


def read_session_token(token: str | None) -> int | None:
    if not token:
        return None
    try:
        user_id, expires, sig = token.split(".")
        if not hmac.compare_digest(sig, _sign(f"{user_id}.{expires}")):
            return None
        if int(expires) < time.time():
            return None
        return int(user_id)
    except Exception:
        return None


def hash_ip(ip: str) -> str:
    # 원본 IP를 저장하지 않는다 (체험 계정 생성 제한용)
    return hashlib.sha256((SECRET_KEY + ":" + ip).encode()).hexdigest()[:32]
