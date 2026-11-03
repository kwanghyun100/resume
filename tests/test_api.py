"""API 동작 확인. 실행: python -m pytest tests  (또는 python tests/test_api.py)"""
import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp) / 'test.db'}"
os.environ["GEMINI_API_KEY"] = ""  # .env의 키보다 우선 (요약 비활성 상태로 테스트)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from backend import vcs  # noqa: E402
from backend.main import app  # noqa: E402


def test_git_long():
    from backend.seed import GIT_STATUS_7
    r = vcs.parse(GIT_STATUS_7)
    assert r["vcs"] == "git" and r["branch"] == "fix/refund-7"
    assert [f["path"] for f in r["files"]] == ["app/services/RefundService.py", "tests/test_refund.py"]


def test_git_long_untracked_korean():
    text = "현재 브랜치 main\n커밋하도록 정하지 않은 변경 사항:\n\t수정함:        a.py\n\n추적하지 않는 파일:\n  (커밋할 사항에 포함하려면 \"git add <파일>...\"을 사용하십시오)\n\tnew.py\n"
    r = vcs.parse(text)
    assert r["branch"] == "main"
    assert r["files"] == [{"status": "M", "path": "a.py"}, {"status": "?", "path": "new.py"}]


def test_git_short():
    r = vcs.parse("## fix/refund-7...origin/fix/refund-7 [ahead 1]\n M src/a.py\nA  src/b.py\n?? notes.txt\nR  old.py -> new.py\n")
    assert r["vcs"] == "git" and r["branch"] == "fix/refund-7"
    assert r["files"] == [
        {"status": "M", "path": "src/a.py"}, {"status": "A", "path": "src/b.py"},
        {"status": "?", "path": "notes.txt"}, {"status": "R", "path": "new.py"},
    ]


def test_git_short_no_branch():
    r = vcs.parse(" M a.py\n?? b.py")
    assert r["vcs"] == "git" and r["branch"] is None and len(r["files"]) == 2


def test_svn():
    r = vcs.parse("M       src\\batch\\Leave.java\n?       new.txt\nD       old.txt\nA  +    copied.txt\n")
    assert r["vcs"] == "svn" and r["branch"] is None
    assert [f["status"] for f in r["files"]] == ["M", "?", "D", "A"]
    assert r["files"][0]["path"] == "src\\batch\\Leave.java"


def test_svn_copied_with_history():
    r = vcs.parse("A  +    src/copied.java\nM       src/a.java\n")
    assert r["vcs"] == "svn" and r["files"][0] == {"status": "A", "path": "src/copied.java"}


def test_concurrent_wrong_pins_lock():
    from concurrent.futures import ThreadPoolExecutor
    TestClient(app).post("/api/login", json={"name": "racer", "pin": "1111", "create": True})
    with ThreadPoolExecutor(10) as ex:
        codes = list(ex.map(lambda _: TestClient(app).post("/api/login", json={"name": "racer", "pin": "0000"}).status_code, range(20)))
    assert codes.count(401) <= 4, codes  # 5번째부터는 잠김
    assert TestClient(app).post("/api/login", json={"name": "racer", "pin": "1111"}).status_code == 423


def test_long_branch_and_briefing_limit():
    from backend import briefing, config
    c = TestClient(app)
    c.post("/api/login", json={"name": "brancher", "pin": "1212", "create": True})
    p = c.post("/api/projects", json={"name": "p"}).json()
    t = c.post(f"/api/projects/{p['id']}/tasks", json={"title": "t"}).json()
    r = c.post(f"/api/tasks/{t['id']}/records", json={"type": "manual", "next": "x", "vcs_raw": "On branch " + "b" * 300})
    assert r.status_code == 200 and len(r.json()["record"]["vcs_branch"]) == 200

    # 하루 호출 한도 (Gemini 호출은 가짜로 대체)
    old_key, old_call, old_limit = config.GEMINI_API_KEY, briefing.call_gemini, config.BRIEFING_PER_USER_PER_DAY
    config.GEMINI_API_KEY, config.BRIEFING_PER_USER_PER_DAY = "fake", 2
    briefing.call_gemini = lambda src: ("① a\n② b\n③ c", "fake-model")
    try:
        for i in range(2):
            c.post(f"/api/tasks/{t['id']}/records", json={"type": "manual", "next": f"n{i}"})
            assert c.post(f"/api/tasks/{t['id']}/briefing").json()["cached"] is False
        assert c.post(f"/api/tasks/{t['id']}/briefing").json()["cached"] is True  # 기록 그대로 → 캐시, 한도 미차감
        c.post(f"/api/tasks/{t['id']}/records", json={"type": "manual", "next": "n9"})
        assert c.post(f"/api/tasks/{t['id']}/briefing").status_code == 429
    finally:
        config.GEMINI_API_KEY, briefing.call_gemini, config.BRIEFING_PER_USER_PER_DAY = old_key, old_call, old_limit


def test_garbage():
    assert vcs.parse("hello world\nfoo")["files"] == []


def test_flow():
    c = TestClient(app)
    assert c.get("/api/me").status_code == 401
    assert c.post("/api/login", json={"name": "kw", "pin": "1234"}).json() == {"need_create": True}
    assert c.post("/api/login", json={"name": "kw", "pin": "1234", "create": True}).json()["created"]
    home = c.get("/api/home").json()
    assert home["project"] is None and home["features"]["briefing"] is False

    p = c.post("/api/projects", json={"name": "결제"}).json()
    t = c.post(f"/api/projects/{p['id']}/tasks", json={"title": "환불", "ticket_no": "#7"}).json()
    home = c.get("/api/home").json()
    assert home["card"]["task"]["id"] == t["id"] and not home["card"]["has_record"]

    # 자동 저장: 같은 내용은 저장 안 함, 연속 자동 기록은 1건 유지
    base = {"what": "환불 수정", "where": "calc_refund", "next": ""}
    assert c.post(f"/api/tasks/{t['id']}/records", json={**base, "type": "auto"}).json()["saved"]
    assert not c.post(f"/api/tasks/{t['id']}/records", json={**base, "type": "auto"}).json()["saved"]
    assert c.post(f"/api/tasks/{t['id']}/records", json={**base, "where": "x", "type": "auto"}).json()["saved"]
    assert len(c.get(f"/api/tasks/{t['id']}/records").json()["records"]) == 1
    card = c.get("/api/home").json()["card"]
    assert card["next"] == "" and card["draft_at"]

    r = c.post(f"/api/tasks/{t['id']}/records", json={**base, "next": "테스트", "type": "manual",
                                                     "vcs_raw": "## main\n M a.py"}).json()
    assert r["record"]["vcs_files"] == [{"status": "M", "path": "a.py"}]
    card = c.get("/api/home").json()["card"]
    assert card["next"] == "테스트" and card["next_from"] == "manual" and card["vcs_branch"] == "main"
    assert c.get("/api/home").json()["project"]["vcs_type"] == "git"

    # 수동 기록 이후 자동 초안: 수동 "다음엔" + 초안 내용 함께
    c.post(f"/api/tasks/{t['id']}/records", json={**base, "where": "새 위치", "type": "auto"})
    card = c.get("/api/home").json()["card"]
    assert card["where"] == "새 위치" and card["next"] == "테스트" and card["draft_at"]

    # 키 없으면 브리핑 비활성
    assert c.post(f"/api/tasks/{t['id']}/briefing").status_code == 404

    # 다른 작업 시작 → 이전 작업 대기
    t2 = c.post(f"/api/projects/{p['id']}/tasks", json={"title": "다른 일"}).json()
    tasks = {x["id"]: x["status"] for x in c.get("/api/home").json()["tasks"]}
    assert tasks == {t["id"]: "waiting", t2["id"]: "active"}

    # 잠금: 5회 실패
    c2 = TestClient(app)
    for i in range(4):
        assert c2.post("/api/login", json={"name": "kw", "pin": "0000"}).status_code == 401
    assert c2.post("/api/login", json={"name": "kw", "pin": "0000"}).status_code == 423
    assert c2.post("/api/login", json={"name": "kw", "pin": "1234"}).status_code == 423


def test_demo():
    c = TestClient(app)
    assert c.post("/api/demo").json()["ok"]
    home = c.get("/api/home").json()
    assert home["user"]["is_demo"] and home["features"]["briefing"]
    card = home["card"]
    assert card["task"]["label"] == "유지보수 #7 — 환불 로직 수정"
    assert card["next"] == "예외 처리 추가 후 단위 테스트 작성"
    assert card["vcs_branch"] == "fix/refund-7" and len(card["vcs_files"]) == 2
    assert len([t for t in home["tasks"] if t["status"] == "done"]) == 6
    recs = c.get(f"/api/tasks/{card['task']['id']}/records").json()["records"]
    assert [r["type"] for r in recs].count("manual") == 3 and [r["type"] for r in recs].count("auto") == 2

    carry = c.get(f"/api/tasks/{card['task']['id']}/draft").json()["carry"]
    assert carry["vcs_raw"].startswith("On branch fix/refund-7") and carry["links"]

    b = c.post(f"/api/tasks/{card['task']['id']}/briefing").json()
    assert b["demo"] and not b["stale"] and b["summary"].startswith("①")

    # 다른 체험 계정과 분리
    other = TestClient(app)
    other.post("/api/demo")
    assert other.get(f"/api/tasks/{card['task']['id']}/records").status_code == 404

    portal = next(p for p in home["projects"] if p["name"] == "사내 포털")
    pc = c.get(f"/api/home?project_id={portal['id']}").json()["card"]
    assert pc["next"] == "" and pc["what"]

    assert c.post("/api/demo/reset").json()["ok"]
    assert c.get("/api/home").json()["card"]["task"]["label"] == "유지보수 #7 — 환불 로직 수정"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
