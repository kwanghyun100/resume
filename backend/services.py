"""기록·카드 조립 로직 (라우트와 체험 데이터 생성에서 함께 사용)."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import KST
from .db import Project, Record, Task, utcnow


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def content_hash(what: str, where: str, next_: str, memo: str, links: list, vcs_raw: str) -> str:
    payload = json.dumps([what, where, next_, memo, links, vcs_raw], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def record_dict(r: Record) -> dict:
    return {
        "id": r.id,
        "type": r.type,
        "what": r.what or "",
        "where": r.where_text or "",
        "next": r.next_text or "",
        "memo": r.memo or "",
        "links": r.links or [],
        "vcs_raw": r.vcs_raw or "",
        "vcs_branch": r.vcs_branch,
        "vcs_files": r.vcs_files or [],
        "created_at": iso(r.created_at),
    }


def task_label(t: Task) -> str:
    return f"{t.ticket_no} — {t.title}" if t.ticket_no else t.title


def task_dict(t: Task, last_record_at: datetime | None = None) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "ticket_no": t.ticket_no or "",
        "label": task_label(t),
        "status": t.status,
        "last_record_at": iso(last_record_at),
        "updated_at": iso(t.updated_at),
    }


def recent_records(db: Session, task_id: int, limit: int = 10) -> list[Record]:
    return list(
        db.scalars(
            select(Record)
            .where(Record.task_id == task_id)
            .order_by(Record.created_at.desc(), Record.id.desc())
            .limit(limit)
        )
    )


def build_card(db: Session, project: Project, task: Task) -> dict:
    """이어하기 카드. 활성 작업의 가장 최근 기록을 기준으로,
    자동 초안이 마지막 수동 기록보다 새로우면 둘을 합쳐 보여준다 (F1 동작 규칙)."""
    recs = recent_records(db, task.id, 20)
    latest = recs[0] if recs else None
    last_manual = next((r for r in recs if r.type == "manual"), None)

    card = {
        "project": {"id": project.id, "name": project.name, "vcs_type": project.vcs_type},
        "task": task_dict(task, latest.created_at if latest else None),
        "has_record": latest is not None,
        "what": "", "where": "", "next": "", "memo": "", "links": [],
        "vcs_branch": None, "vcs_files": [], "vcs_raw": "",
        "next_from": None,       # "manual" | "auto" | None
        "draft_at": None,        # 수동 기록 이후 자동 초안 시각
        "manual_at": iso(last_manual.created_at) if last_manual else None,
        "last_at": iso(latest.created_at) if latest else None,
        "last_type": latest.type if latest else None,
    }
    if not latest:
        return card

    draft = latest if latest.type == "auto" else None
    base = last_manual
    # 표시 필드: 초안 값 우선, 비었으면 마지막 수동 기록 값
    for key, attr in (("what", "what"), ("where", "where_text"), ("memo", "memo")):
        v = (getattr(draft, attr) if draft else "") or (getattr(base, attr) if base else "")
        card[key] = v or ""
    card["links"] = (draft.links if draft and draft.links else None) or (base.links if base else []) or []
    src_vcs = draft if draft and draft.vcs_raw else base
    if src_vcs:
        card["vcs_branch"] = src_vcs.vcs_branch
        card["vcs_files"] = src_vcs.vcs_files or []
        card["vcs_raw"] = src_vcs.vcs_raw or ""
    if draft and draft.next_text:
        card["next"], card["next_from"] = draft.next_text, "auto"
    elif base and base.next_text:
        card["next"], card["next_from"] = base.next_text, "manual"
    if draft:
        card["draft_at"] = iso(draft.created_at)
    return card


# ---------------------------------------------------------------- 주간 통계 · 스탠드업

def _kst_date(dt: datetime):
    return dt.replace(tzinfo=timezone.utc).astimezone(KST).date()


def _user_records_since(db: Session, user_id: int, since: datetime, type_: str | None = None):
    q = (
        select(Record, Task, Project)
        .join(Task, Record.task_id == Task.id)
        .join(Project, Task.project_id == Project.id)
        .where(Project.user_id == user_id, Record.created_at >= since)
        .order_by(Record.created_at.desc(), Record.id.desc())
    )
    if type_:
        q = q.where(Record.type == type_)
    return db.execute(q).all()


def week_stats(db: Session, user_id: int) -> dict:
    """최근 7일 멈춤 기록 수, 완료한 작업 수, 연속 기록 일수."""
    now = utcnow()
    rows = _user_records_since(db, user_id, now - timedelta(days=60))
    week_ago = now - timedelta(days=7)
    manual_week = sum(1 for r, _, _ in rows if r.type == "manual" and r.created_at >= week_ago)
    auto_week = sum(1 for r, _, _ in rows if r.type == "auto" and r.created_at >= week_ago)
    done_week = db.scalar(
        select(func.count(Task.id)).join(Project, Task.project_id == Project.id)
        .where(Project.user_id == user_id, Task.status == "done", Task.updated_at >= week_ago)
    ) or 0
    days = {_kst_date(r.created_at) for r, _, _ in rows if r.type == "manual"}
    today = datetime.now(KST).date()
    day = today if today in days else today - timedelta(days=1)
    streak = 0
    while day in days:
        streak += 1
        day -= timedelta(days=1)
    return {"manual": manual_week, "auto": auto_week, "done": done_week, "streak": streak}


_DOW = "월화수목금토일"


def _fmt_day(d) -> str:
    return f"{d.month}/{d.day}({_DOW[d.weekday()]})"


def standup(db: Session, user_id: int) -> dict:
    """마지막으로 기록한 날의 한 일 + 오늘 이어갈 일을 마크다운으로 만든다 (AI 없이)."""
    today = datetime.now(KST).date()
    rows = _user_records_since(db, user_id, utcnow() - timedelta(days=14), "manual")
    prev_days = sorted({_kst_date(r.created_at) for r, _, _ in rows if _kst_date(r.created_at) < today}, reverse=True)
    prev_day = prev_days[0] if prev_days else None

    done_lines, seen = [], set()
    if prev_day:
        for r, t, p in rows:  # 최신순: 작업마다 그날의 마지막 기록만
            if _kst_date(r.created_at) != prev_day or t.id in seen:
                continue
            seen.add(t.id)
            where = r.where_text or r.what or "기록"
            done_lines.append(f"- [{p.name}] {task_label(t)}: {where}" + (" ✅ 완료" if t.status == "done" else ""))

    todo_lines = []
    projects = db.scalars(select(Project).where(Project.user_id == user_id).order_by(Project.created_at, Project.id))
    for p in projects:
        t = db.get(Task, p.active_task_id) if p.active_task_id else None
        if not t or t.status != "active":
            continue
        card = build_card(db, p, t)
        todo_lines.append(f"- [{p.name}] {task_label(t)}: {card['next'] or '(다음엔 미기록 — 이어하기 카드에서 적어주세요)'}")

    parts = [f"### 스탠드업 · {_fmt_day(today)}", ""]
    parts.append(f"**지난 작업 일 ({_fmt_day(prev_day)})**" if prev_day else "**지난 작업 일**")
    parts += done_lines or ["- (최근 2주 멈춤 기록 없음)"]
    parts += ["", "**오늘 할 일**"]
    parts += todo_lines or ["- (진행 중인 작업 없음)"]
    return {"text": "\n".join(parts), "prev_day": prev_day.isoformat() if prev_day else None}
