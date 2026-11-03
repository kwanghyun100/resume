"""체험 모드 샘플 데이터. 시각은 '지금' 기준 상대값(한국 시간)으로 만든다."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import vcs
from .briefing import build_source, source_hash
from .config import KST
from .db import Briefing, BriefingCall, Project, Record, Suggestion, Task, User, utcnow
from .services import content_hash, record_dict, recent_records, task_label

GIT_STATUS_7 = """On branch fix/refund-7
Your branch is ahead of 'origin/fix/refund-7' by 1 commit.
  (use "git push" to publish your local commits)

Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)
  (use "git restore <file>..." to discard changes in working directory)
\tmodified:   app/services/RefundService.py
\tmodified:   tests/test_refund.py

no changes added to commit (use "git add" and/or "git commit -a")"""

SVN_STATUS_HR = """M       src/batch/LeaveCalcBatch.java
M       src/batch/LeaveCalcBatch.xml
?       src/batch/LeaveCalcBatchTest.java"""

BRIEFING_7 = (
    "① 하고 있던 일: 부분 환불 시 환불 금액이 잘못 계산되는 오류 수정\n"
    "② 멈춘 지점: RefundService.py calc_refund()의 부분 환불 분기 작성 완료 (fix/refund-7)\n"
    "③ 다음 할 일: 쿠폰 미적용 건 예외 처리 추가 → test_refund.py 단위 테스트 작성"
)

# 체험 모드의 멈춤 기록 추천 (미리 만든 AI 결과 — 체험 방문자 입력으로는 API를 호출하지 않는다)
DEMO_SUGGEST = {
    "유지보수 #7": {
        "where": "calc_refund() 부분 환불 분기 완료, 쿠폰 미적용 건 예외 처리 전",
        "next": ["쿠폰 미적용 주문 예외 처리 추가", "test_refund.py 부분 환불 테스트 작성", "금요일 배포 전 MR #42 리뷰 요청"],
    },
    "요청 #112": {
        "where": "LeaveCalcBatch.java calcMonthlyLeave() 월 단위 발생 로직 수정 완료",
        "next": ["LeaveCalcBatchTest 1년 미만자 케이스 작성", "개발서버에서 배치 수동 실행해 결과 확인", "svn 커밋 전 변경 파일 3개 검토"],
    },
    "유지보수 #21": {
        "where": "FileDownloadController Content-Disposition 헤더 인코딩 확인 중",
        "next": ["filename*=UTF-8 형식으로 헤더 수정", "한글 파일명 첨부로 브라우저별 다운로드 확인", "IE 모드 사용 부서 호환 여부 확인"],
    },
}

DONE_TASKS = [
    ("유지보수 #1", "결제 완료 메일 발송 누락", "메일 큐 재시도 로직 추가 · 배포 완료"),
    ("유지보수 #2", "카드사 응답 코드 매핑 오류", "응답 코드 표 갱신 · 배포 완료"),
    ("유지보수 #3", "정산 리포트 합계 불일치", "반올림 위치 수정 · 검증 완료"),
    ("유지보수 #4", "결제창 모바일 레이아웃 깨짐", "CSS 수정 · 배포 완료"),
    ("유지보수 #5", "가상계좌 입금 확인 지연", "웹훅 타임아웃 조정 · 배포 완료"),
    ("유지보수 #6", "영수증 PDF 한글 깨짐", "폰트 임베딩 · 배포 완료"),
]
WAITING_TASKS = [
    ("유지보수 #8", "할부 개월 수 표시 오류"),
    ("유지보수 #9", "관리자 결제 취소 권한 점검"),
    ("유지보수 #10", "결제 로그 보관 기간 정책 반영"),
]


def _kst(day_offset: int, hh: int, mm: int) -> datetime:
    """오늘(KST)로부터 day_offset일 전 hh:mm (KST) → naive UTC."""
    today = datetime.now(KST).date()
    local = datetime(today.year, today.month, today.day, hh, mm, tzinfo=KST) - timedelta(days=day_offset)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def _record(task_id: int, type_: str, at: datetime, what="", where="", next_="", memo="", links=None, vcs_raw="") -> Record:
    links = links or []
    parsed = vcs.parse(vcs_raw) if vcs_raw else {"branch": None, "files": []}
    return Record(
        task_id=task_id, type=type_, what=what, where_text=where, next_text=next_,
        memo=memo, links=links, vcs_raw=vcs_raw,
        vcs_branch=parsed["branch"], vcs_files=parsed["files"],
        content_hash=content_hash(what, where, next_, memo, links, vcs_raw),
        created_at=at,
    )


def clear_user_data(db: Session, user_id: int) -> None:
    project_ids = select(Project.id).where(Project.user_id == user_id)
    task_ids = select(Task.id).where(Task.project_id.in_(project_ids))
    db.execute(delete(Briefing).where(Briefing.task_id.in_(task_ids)))
    db.execute(delete(Suggestion).where(Suggestion.task_id.in_(task_ids)))
    db.execute(delete(BriefingCall).where(BriefingCall.user_id == user_id))
    db.execute(delete(Record).where(Record.task_id.in_(task_ids)))
    db.execute(delete(Task).where(Task.project_id.in_(project_ids)))
    db.execute(delete(Project).where(Project.user_id == user_id))


def seed_demo(db: Session, user: User) -> None:
    # ① 결제시스템 (Git)
    pay = Project(user_id=user.id, name="결제시스템", vcs_type="git")
    db.add(pay)
    db.flush()
    for i, (no, title, done_note) in enumerate(DONE_TASKS):
        t = Task(project_id=pay.id, title=title, ticket_no=no, status="done",
                 created_at=_kst(14 - i, 9, 0), updated_at=_kst(9 - i, 17, 30))
        db.add(t)
        db.flush()
        db.add(_record(t.id, "manual", _kst(9 - i, 17, 30), what=title, where=done_note, next_="완료"))

    t7 = Task(project_id=pay.id, title="환불 로직 수정", ticket_no="유지보수 #7", status="active",
              created_at=_kst(3, 9, 10), updated_at=_kst(1, 18, 12))
    db.add(t7)
    db.flush()
    pay.active_task_id = t7.id
    db.add_all([
        _record(t7.id, "manual", _kst(2, 10, 5),
                what="환불 금액 계산 오류 수정",
                where="RefundService.py — 재현 케이스 확인 (쿠폰+부분 환불 시 금액 음수)",
                next_="calc_refund() 흐름 따라가며 원인 위치 찾기",
                memo="[메일] 고객센터 김OO: 11/1 접수 건 3건 모두 쿠폰 사용 주문"),
        _record(t7.id, "auto", _kst(2, 15, 40),
                what="환불 금액 계산 오류 수정",
                where="calc_refund() 안에서 쿠폰 할인액을 두 번 빼는 부분 발견",
                next_=""),
        _record(t7.id, "manual", _kst(2, 18, 30),
                what="환불 금액 계산 오류 수정",
                where="RefundService.py calc_refund() — 쿠폰 이중 차감 원인 확인",
                next_="부분 환불 분기 새로 작성"),
        _record(t7.id, "auto", _kst(1, 11, 20),
                what="환불 금액 계산 오류 수정",
                where="calc_refund() 부분 환불 분기 절반 작성",
                next_="",
                memo="[회의록] 10:00 주간회의 — 환불 건은 이번 주 금요일 배포 목표"),
        _record(t7.id, "manual", _kst(1, 18, 12),
                what="환불 금액 계산 오류 수정",
                where="RefundService.py calc_refund() — 부분 환불 분기 작성 완료",
                next_="예외 처리 추가 후 단위 테스트 작성",
                memo="[일정] 금 14:00 환불 수정 배포 (운영팀 승인 완료)",
                links=["https://git.example.local/payment/merge_requests/42"],
                vcs_raw=GIT_STATUS_7),
    ])
    for no, title in WAITING_TASKS:
        db.add(Task(project_id=pay.id, title=title, ticket_no=no, status="waiting",
                    created_at=_kst(3, 9, 15), updated_at=_kst(3, 9, 15)))

    # ② 인사관리 (SVN)
    hr = Project(user_id=user.id, name="인사관리", vcs_type="svn")
    db.add(hr)
    db.flush()
    hr1 = Task(project_id=hr.id, title="연차 일수 계산 배치 수정", ticket_no="요청 #112", status="active",
               created_at=_kst(5, 9, 0), updated_at=_kst(1, 16, 5))
    hr2 = Task(project_id=hr.id, title="인사발령 엑셀 업로드 검증 추가", ticket_no="요청 #115", status="waiting",
               created_at=_kst(4, 9, 0), updated_at=_kst(4, 9, 0))
    hr3 = Task(project_id=hr.id, title="퇴직자 계정 자동 잠금", ticket_no="요청 #98", status="done",
               created_at=_kst(10, 9, 0), updated_at=_kst(6, 17, 0))
    db.add_all([hr1, hr2, hr3])
    db.flush()
    hr.active_task_id = hr1.id
    db.add_all([
        _record(hr3.id, "manual", _kst(6, 17, 0), what="퇴직자 계정 자동 잠금", where="스케줄러 등록 완료", next_="완료"),
        _record(hr1.id, "manual", _kst(1, 16, 5),
                what="입사일 기준 연차 일수 계산 오류 수정 (1년 미만자)",
                where="LeaveCalcBatch.java calcMonthlyLeave() — 월 단위 발생 로직 수정",
                next_="LeaveCalcBatchTest 작성 후 개발서버에서 배치 수동 실행",
                vcs_raw=SVN_STATUS_HR),
    ])

    # ③ 버튼을 안 누르고 퇴근한 날 (다음엔 비어 있음)
    portal = Project(user_id=user.id, name="사내 포털", vcs_type="git")
    db.add(portal)
    db.flush()
    p1 = Task(project_id=portal.id, title="공지사항 첨부파일 다운로드 오류", ticket_no="유지보수 #21", status="active",
              created_at=_kst(1, 13, 0), updated_at=_kst(1, 17, 48))
    db.add(p1)
    db.flush()
    portal.active_task_id = p1.id
    db.add(_record(p1.id, "auto", _kst(1, 17, 48),
                   what="공지사항 첨부파일 한글 파일명 다운로드 시 깨짐",
                   where="FileDownloadController — Content-Disposition 헤더 인코딩 확인 중",
                   next_=""))

    user.last_project_id = pay.id
    db.flush()

    # 미리 만든 복귀 브리핑 (체험 모드는 API를 호출하지 않는다)
    recs = [record_dict(r) for r in recent_records(db, t7.id, 10)]
    src = build_source(pay.name, task_label(t7), recs)
    db.add(Briefing(task_id=t7.id, summary=BRIEFING_7, source_hash=source_hash(src),
                    model="sample (미리 생성)", created_at=utcnow()))
