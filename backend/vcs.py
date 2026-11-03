"""`git status` / `git status --short` / `svn status` 붙여넣기 해석.

결과: {"vcs": "git"|"svn"|"unknown", "branch": str|None, "files": [{"status", "path"}]}
해석에 실패해도 예외를 던지지 않고 빈 목록을 돌려준다 (원문은 호출 측에서 그대로 저장).
"""
import re

# git status (긴 형식) — 영어/한국어 로케일
_GIT_BRANCH = [
    re.compile(r"^On branch (.+)$"),
    re.compile(r"^현재 브랜치 (.+)$"),
]
_GIT_DETACHED = re.compile(r"^(HEAD detached (?:at|from) .+|HEAD가 .+ 위치에서 분리되었습니다.*)$")
_GIT_LONG_ENTRY = re.compile(
    r"^\s+(modified|new file|deleted|renamed|copied|typechange|both modified|both added|"
    r"수정함|새 파일|삭제함|이름 바꿈|복사함|양쪽에서 수정|양쪽에서 추가)\s*:\s+(.+)$"
)
_GIT_LONG_STATUS = {
    "modified": "M", "수정함": "M", "typechange": "M",
    "new file": "A", "새 파일": "A", "copied": "A", "복사함": "A",
    "deleted": "D", "삭제함": "D",
    "renamed": "R", "이름 바꿈": "R",
    "both modified": "U", "both added": "U", "양쪽에서 수정": "U", "양쪽에서 추가": "U",
}
_GIT_SECTION_UNTRACKED = re.compile(r"^(Untracked files|추적하지 않는 파일)\s*:")
_GIT_SECTION_OTHER = re.compile(
    r"^(Changes to be committed|Changes not staged for commit|Unmerged paths|"
    r"커밋할 변경 사항|커밋하도록 정하지 않은 변경 사항|병합하지 않은 경로)\s*:"
)
_GIT_LONG_HINTS = re.compile(
    r"^(On branch|현재 브랜치|Changes to be committed|Changes not staged|Untracked files|"
    r"nothing to commit|Your branch|커밋할 변경 사항|커밋하도록 정하지 않은|추적하지 않는 파일|"
    r"브랜치가 .+ 기준으로|커밋할 사항 없음)"
)

# git status --short [-b]
_GIT_SHORT_BRANCH = re.compile(r"^## (?:No commits yet on )?([^\s.]+(?:\.[^\s.]+)*?)(?:\.\.\.\S+)?(?: \[.*\])?$")
_GIT_SHORT_ENTRY = re.compile(r"^([ MADRCUT?!])([ MADRCUT?!]) (.+)$")

# svn status: 7~9개 상태 컬럼 + 공백 + 경로
_SVN_ENTRY = re.compile(r"^([ ACDIMRX?!~])[ CM][ L][ +][ SX][ KOTB][ C]?[ *]?\s+(?:\d+\s+)?(\S.*)$")
_SVN_HINTS = re.compile(r"^(Status against revision|Performing status on external|svn: |--- Changelist)")


def _norm_status(code: str) -> str:
    code = code.strip()
    if code in ("?", "??"):
        return "?"
    for c in code:
        if c in "MADRU":
            return c
        if c == "C":  # git copied / svn conflicted
            return "U"
        if c == "!":
            return "D"
        if c == "~" or c == "T":
            return "M"
    return code[:1] or "M"


def _add(files: list, seen: set, status: str, path: str) -> None:
    path = path.strip().strip('"')
    if not path or path in seen:
        return
    seen.add(path)
    files.append({"status": status, "path": path})


def detect(text: str) -> str:
    lines = [l.rstrip("\r") for l in text.splitlines() if l.strip()]
    if not lines:
        return "unknown"
    if any(_GIT_LONG_HINTS.match(l) for l in lines) or any(l.startswith("## ") for l in lines):
        return "git"
    if any(_SVN_HINTS.match(l) for l in lines):
        return "svn"
    git_short = svn = 0
    for l in lines:
        # svn은 경로가 8번째 컬럼 이후, git --short는 4번째 컬럼에서 시작
        m = re.match(r"^(\S{1,2}|\s\S)(\s+)\S", l)
        if not m:
            continue
        prefix_len = len(m.group(1)) + len(m.group(2))
        if prefix_len >= 6 and _SVN_ENTRY.match(l):
            svn += 1
        elif prefix_len == 3 and _GIT_SHORT_ENTRY.match(l):
            git_short += 1
    if svn > git_short:
        return "svn"
    if git_short:
        return "git"
    return "unknown"


def parse(text: str) -> dict:
    result = {"vcs": "unknown", "branch": None, "files": []}
    if not text or not text.strip():
        return result
    try:
        vcs = detect(text)
        result["vcs"] = vcs
        files: list = []
        seen: set = set()
        lines = [l.rstrip("\r") for l in text.splitlines()]

        if vcs == "git":
            in_untracked = False
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                for pat in _GIT_BRANCH:
                    m = pat.match(stripped)
                    if m:
                        result["branch"] = m.group(1).strip()
                if _GIT_DETACHED.match(stripped) and not result["branch"]:
                    result["branch"] = stripped
                m = _GIT_SHORT_BRANCH.match(line)
                if m:
                    result["branch"] = m.group(1)
                    continue
                if _GIT_SECTION_UNTRACKED.match(stripped):
                    in_untracked = True
                    continue
                if _GIT_SECTION_OTHER.match(stripped):
                    in_untracked = False
                    continue
                m = _GIT_LONG_ENTRY.match(line)
                if m:
                    path = m.group(2)
                    if "->" in path:
                        path = path.split("->")[-1]
                    _add(files, seen, _GIT_LONG_STATUS.get(m.group(1), "M"), path)
                    continue
                if in_untracked and line[:1] in ("\t", " ") and not stripped.startswith("("):
                    _add(files, seen, "?", stripped)
                    continue
                m = _GIT_SHORT_ENTRY.match(line)
                if m and not line.startswith("  ("):
                    path = m.group(3)
                    if " -> " in path:
                        path = path.split(" -> ")[-1]
                    _add(files, seen, _norm_status(m.group(1) + m.group(2)), path)

        elif vcs == "svn":
            for line in lines:
                if not line.strip() or _SVN_HINTS.match(line):
                    continue
                m = _SVN_ENTRY.match(line)
                if m:
                    _add(files, seen, _norm_status(m.group(1)), m.group(2))

        result["files"] = files
    except Exception:
        result["files"] = []
    return result
