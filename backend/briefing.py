"""복귀 브리핑 (Gemini API). 버튼을 눌렀을 때만 호출하고, 입력이 같으면 캐시를 쓴다."""
import hashlib
import json
import time
import urllib.error
import urllib.request

from .config import GEMINI_API_KEY, GEMINI_FALLBACK_MODELS, GEMINI_MODEL

MAX_RECORDS = 10


def build_source(project_name: str, task_label: str, records: list[dict]) -> str:
    """Gemini로 보낼 텍스트. 전송 범위: 최근 기록 최대 10건, 메모, 수정 파일 목록 (8.6절)."""
    lines = [f"프로젝트: {project_name}", f"작업: {task_label}", ""]
    for i, r in enumerate(records[:MAX_RECORDS], 1):
        kind = "수동 기록" if r["type"] == "manual" else "자동 저장 초안"
        lines.append(f"[{i}] {r['created_at']} ({kind})")
        for label, key in (("무엇을", "what"), ("어디까지", "where"), ("다음엔", "next"), ("메모", "memo")):
            if r.get(key):
                lines.append(f"- {label}: {r[key]}")
        if r.get("vcs_branch"):
            lines.append(f"- 브랜치: {r['vcs_branch']}")
        if r.get("vcs_files"):
            lines.append("- 수정 파일: " + ", ".join(f"{f['status']} {f['path']}" for f in r["vcs_files"]))
        lines.append("")
    return "\n".join(lines).strip()


def source_hash(source: str) -> str:
    return hashlib.sha256(source.encode()).hexdigest()


PROMPT = """너는 개발자의 작업 복귀를 돕는 비서다. 아래는 한 작업의 최근 기록이다 (최신순).
회의나 호출에서 막 돌아온 개발자가 10초 안에 다시 작업을 시작할 수 있도록, 정확히 아래 3줄 형식으로만 한국어로 답하라.
각 줄은 60자 이내로, 파일명·함수명 같은 구체적인 위치를 살려라. 기록에 없는 내용은 지어내지 마라.

① 하고 있던 일: ...
② 멈춘 지점: ...
③ 다음 할 일: ...

--- 기록 ---
{source}
"""


class BriefingError(Exception):
    pass


def _request(model: str, prompt: str, thinking: bool, timeout: float) -> dict:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    # thinking 모델은 생각 토큰도 출력 한도에 포함되므로 한도를 넉넉히 두고 생각 수준을 낮춘다
    gen = {"temperature": 0.2, "maxOutputTokens": 4096}
    if thinking:
        gen["thinkingConfig"] = {"thinkingLevel": "low"}
    req = urllib.request.Request(
        url,
        data=json.dumps({"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gen}).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# 모델이 없거나(404: 신규 사용자 대상 종료 등) 일시적으로 바쁠 때는 다음 모델로 넘어간다
_RETRY_NEXT_MODEL = {404, 429, 500, 503, 504}


def call_gemini(source: str) -> tuple[str, str]:
    """(요약, 실제 사용한 모델)을 돌려준다."""
    if not GEMINI_API_KEY:
        raise BriefingError("no_key")
    prompt = PROMPT.format(source=source)
    models = list(dict.fromkeys([GEMINI_MODEL, *GEMINI_FALLBACK_MODELS]))
    deadline = time.monotonic() + 45
    last = "network"
    for model in models:
        remaining = deadline - time.monotonic()
        if remaining < 3:
            break
        try:
            try:
                data = _request(model, prompt, thinking=True, timeout=min(25, remaining))
            except urllib.error.HTTPError as e:
                if e.code != 400:
                    raise
                # thinkingLevel을 모르는 모델이면 생각 설정 없이 다시 요청
                data = _request(model, prompt, thinking=False, timeout=min(25, deadline - time.monotonic()))
        except urllib.error.HTTPError as e:
            last = f"http_{e.code}"
            if e.code in _RETRY_NEXT_MODEL:
                continue
            raise BriefingError(last) from e
        except Exception:  # 네트워크 오류, 타임아웃
            last = "network"
            continue
        try:
            cand = data["candidates"][0]
            text = "".join(p.get("text", "") for p in cand["content"]["parts"]).strip()
        except (KeyError, IndexError, TypeError) as e:
            raise BriefingError("bad_response") from e
        kept = [l.strip() for l in text.splitlines() if l.strip().startswith(("①", "②", "③"))]
        if len(kept) >= 3:
            return "\n".join(kept[:3]), model
        if cand.get("finishReason") == "MAX_TOKENS" or not text:
            last = "truncated"
            continue
        return text, model
    raise BriefingError(last)
