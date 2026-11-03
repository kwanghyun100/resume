"""기록·카드 조립 로직 (라우트와 체험 데이터 생성에서 함께 사용)."""
import hashlib
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Project, Record, Task


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
