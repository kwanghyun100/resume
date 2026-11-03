"""멈춤 기록 추천: '어디까지'와 '다음엔' 후보를 미리 만들어 사용자는 고르기만 하게 한다.
AI(Gemini)를 쓸 수 있으면 AI가, 없거나 실패하면 규칙으로 만든다. 화면·코드 내용은 보내지 않는다."""
import json
import re

from . import briefing

MAX_RECORDS = 5
MAX_NEXT = 3
TEST_RE = re.compile(r"(^|[/\\._-])tests?([/\\._-]|$)|Test\.|_test\.|\.spec\.", re.I)


def _basename(path: str) -> str:
    return re.split(r"[/\\]", path)[-1]


def _current_files(current: dict, records: list[dict]) -> tuple[list[dict], str | None]:
    """지금 붙여넣은 VCS 상태가 있으면 그것을, 없으면 가장 최근 기록의 것을 쓴다."""
    if current.get("files"):
        return current["files"], current.get("branch")
    for r in records:
        if r.get("vcs_files"):
            return r["vcs_files"], r.get("vcs_branch")
    return [], current.get("branch")


def rule_based(current: dict, records: list[dict]) -> dict:
    """AI 없이 만드는 추천. 지난번 '다음엔' → 수정 파일 → 브랜치 순으로 후보를 만든다."""
    files, branch = _current_files(current, records)
    names = [_basename(f["path"]) for f in files if f.get("status") != "D"]
    code = [n for n in names if not TEST_RE.search(n)]
    tests = [n for n in names if TEST_RE.search(n)]

    nexts = []
    prev_next = next((r["next"] for r in records if r.get("next") and r["next"] != "완료"), "")
    if prev_next:
        nexts.append(prev_next)
    if code and not tests:
        nexts.append(f"{code[0]} 변경분 단위 테스트 작성")
    elif tests:
        nexts.append(f"{tests[0]} 실행해서 통과 확인")
    if names:
        nexts.append(f"수정한 파일 {len(names)}개 검토 후 {branch + '에 ' if branch else ''}커밋")
    if not nexts and current.get("where"):
        nexts.append(f"{current['where'][:40]} 이어서 진행")

    where = ""
    if names and not current.get("where"):
        where = ", ".join(names[:2]) + (f" 외 {len(names) - 2}개" if len(names) > 2 else "") + " 수정 중"
        if branch:
            where += f" ({branch})"
    return {"where": where, "next": list(dict.fromkeys(nexts))[:MAX_NEXT]}


def build_source(project_name: str, task_label: str, current: dict, records: list[dict]) -> str:
    lines = ["--- 지금 입력 중 ---"]
    for label, key in (("무엇을", "what"), ("어디까지", "where"), ("메모", "memo")):
        if current.get(key):
            lines.append(f"- {label}: {current[key][:2000]}")
    if current.get("branch"):
        lines.append(f"- 브랜치: {current['branch']}")
    if current.get("files"):
        lines.append("- 수정 파일: " + ", ".join(f"{f['status']} {f['path']}" for f in current["files"][:30]))
    lines += ["", "--- 최근 기록 (최신순) ---", briefing.build_source(project_name, task_label, records[:MAX_RECORDS])]
    return "\n".join(lines)


PROMPT = """너는 개발자의 '멈춤 기록'을 돕는 비서다. 개발자는 회의나 상급자 호출로 지금 막 자리를 떠야 한다.
아래 정보를 보고, 개발자가 고르기만 하면 되도록 다음 JSON 형식으로만 한국어로 답하라.

{{"where": "어디까지 했는지 한 줄", "next": ["다음에 할 일 후보 1", "후보 2", "후보 3"]}}

규칙:
- where: 60자 이내. 파일·함수명 등 구체적인 위치를 살린다.
- next: 서로 다른 구체적인 행동 1~3개, 각 40자 이내. '… 작성', '… 확인'처럼 바로 실행할 수 있는 말로 끝낸다.
  가장 그럴듯한 것을 먼저 쓴다. 이미 끝난 일은 제안하지 않는다.
- 기록에 없는 파일·사실은 지어내지 않는다.

{source}
"""


def _parse(text: str, finish: str | None):
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except ValueError:
        return None  # 잘렸거나 형식이 깨짐 → 다음 모델
    if not isinstance(data, dict):
        return None
    nexts = data.get("next") or []
    if isinstance(nexts, str):
        nexts = [nexts]
    nexts = [str(n).strip()[:80] for n in nexts if str(n).strip()][:MAX_NEXT]
    if not nexts:
        return None
    return {"where": str(data.get("where") or "").strip()[:120], "next": list(dict.fromkeys(nexts))}


def call_ai(source: str) -> tuple[dict, str]:
    return briefing.generate(PROMPT.format(source=source), _parse, json_mode=True)
