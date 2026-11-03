"""이어하기 API (FastAPI). 모든 조회는 현재 사용자(user_id) 조건을 건다."""
import re
import secrets
from datetime import timedelta
from pathlib import Path
from typing import Iterator

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import briefing as briefing_mod
from . import config, seed, vcs
from .db import Briefing, BriefingCall, Project, Record, SessionLocal, Task, User, init_db, utcnow
from .security import hash_ip, hash_pin, make_session_token, read_session_token, verify_pin
from .services import build_card, content_hash, iso, recent_records, record_dict, task_dict, task_label

app = FastAPI(title="이어하기", docs_url=None, redoc_url=None, openapi_url=None)

DEMO_PREFIX = "__demo_"


# ---------------------------------------------------------------- 공통

def get_db() -> Iterator[Session]:
    init_db()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = read_session_token(request.cookies.get(config.SESSION_COOKIE))
    user = db.get(User, uid) if uid else None
    if not user:
        raise HTTPException(401, "로그인이 필요합니다")
    if user.is_demo and user.expires_at and user.expires_at < utcnow():
        raise HTTPException(401, "체험 시간이 끝났습니다. 다시 체험하기를 눌러주세요")
    return user


def _is_https(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


def _set_session(response: Response, request: Request, user: User) -> None:
    max_age = int((user.expires_at - utcnow()).total_seconds()) if user.is_demo and user.expires_at \
        else config.SESSION_DAYS * 86400
    response.set_cookie(
        config.SESSION_COOKIE, make_session_token(user.id), max_age=max_age,
        httponly=True, secure=_is_https(request), samesite="lax", path="/",
    )


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    ip = fwd.split(",")[0].strip() if fwd else (
        request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown"))
    if ":" in ip:  # IPv6는 /64 단위로 묶는다 (주소를 바꿔가며 제한을 피하지 못하도록)
        ip = ":".join(ip.split(":")[:4]) + "::/64"
    return ip


def _own_project(db: Session, user: User, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if not p or p.user_id != user.id:
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다")
    return p


def _own_task(db: Session, user: User, task_id: int) -> tuple[Task, Project]:
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "작업을 찾을 수 없습니다")
    p = _own_project(db, user, t.project_id)
    return t, p


def _features(user: User) -> dict:
    return {"briefing": bool(config.GEMINI_API_KEY) or user.is_demo}


def _user_dict(user: User) -> dict:
    return {
        "name": "체험 사용자" if user.is_demo else user.name,
        "is_demo": user.is_demo,
        "expires_at": iso(user.expires_at),
    }


def _cleanup_expired_demo(db: Session) -> int:
    ids = list(db.scalars(select(User.id).where(User.is_demo.is_(True), User.expires_at < utcnow())))
    for uid in ids:
        seed.clear_user_data(db, uid)
    if ids:
        db.execute(delete(User).where(User.id.in_(ids)))
    db.commit()
    return len(ids)


# ---------------------------------------------------------------- 인증

class LoginIn(BaseModel):
    name: str = Field(max_length=30)
    pin: str
    create: bool = False


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    # 배포 점검용: 어떤 DB에 붙었는지만 알려준다 (주소·비밀번호는 노출하지 않음)
    db.execute(select(1))
    return {"ok": True, "db": db.get_bind().dialect.name}


@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return {"user": _user_dict(user), "features": _features(user)}


@app.post("/api/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    name = body.name.strip()
    if not name or name.startswith(DEMO_PREFIX):
        raise HTTPException(400, "이름을 확인해주세요")
    if not re.fullmatch(r"\d{4}", body.pin):
        raise HTTPException(400, "PIN은 숫자 4자리입니다")

    user = db.scalar(select(User).where(User.name == name, User.is_demo.is_(False)))
    if not user:
        if not body.create:
            return {"need_create": True}
        ip_h = hash_ip(_client_ip(request))
        recent = db.scalar(select(func.count(User.id)).where(
            User.is_demo.is_(False), User.created_ip == ip_h, User.created_at > utcnow() - timedelta(hours=1),
        ))
        if recent >= config.SIGNUP_PER_IP_PER_HOUR:
            raise HTTPException(429, "계정을 너무 많이 만들었습니다. 잠시 후 다시 시도하세요")
        user = User(name=name, pin_hash=hash_pin(body.pin), created_ip=ip_h)
        db.add(user)
        try:
            db.commit()
        except IntegrityError:  # 같은 이름으로 동시에 가입
            db.rollback()
            raise HTTPException(409, "이미 있는 이름입니다. 다시 로그인하세요")
        _set_session(response, request, user)
        return {"ok": True, "created": True}

    now = utcnow()
    if user.locked_until and user.locked_until > now:
        raise _locked_error(user.locked_until - now)
    if user.locked_until:  # 잠금이 끝났으면 카운터를 새로 시작
        db.execute(update(User).where(User.id == user.id, User.locked_until <= now)
                   .values(failed_count=0, locked_until=None))

    # PIN을 확인하기 전에 시도 횟수를 원자적으로 올린다.
    # 동시에 여러 요청이 와도 각 요청이 고유한 순번을 받으므로 5회 이후 시도는 PIN 검사 없이 막힌다.
    attempt = db.execute(
        update(User).where(User.id == user.id).values(failed_count=User.failed_count + 1).returning(User.failed_count)
    ).scalar_one()
    db.commit()
    if attempt > config.LOCK_THRESHOLD:
        db.execute(update(User).where(User.id == user.id, User.locked_until.is_(None))
                   .values(locked_until=now + timedelta(minutes=config.LOCK_MINUTES)))
        db.commit()
        raise _locked_error(timedelta(minutes=config.LOCK_MINUTES))

    if not verify_pin(body.pin, user.pin_hash):
        if attempt >= config.LOCK_THRESHOLD:
            db.execute(update(User).where(User.id == user.id)
                       .values(locked_until=now + timedelta(minutes=config.LOCK_MINUTES)))
            db.commit()
            raise HTTPException(423, f"PIN을 {config.LOCK_THRESHOLD}회 틀려 {config.LOCK_MINUTES}분간 잠겼습니다")
        raise HTTPException(401, f"PIN이 맞지 않습니다 (남은 시도 {config.LOCK_THRESHOLD - attempt}회)")

    db.execute(update(User).where(User.id == user.id).values(failed_count=0, locked_until=None))
    db.commit()
    _set_session(response, request, user)
    return {"ok": True}


def _locked_error(remaining: timedelta) -> HTTPException:
    secs = max(1, int(remaining.total_seconds()) + 1)
    return HTTPException(423, f"PIN을 {config.LOCK_THRESHOLD}회 틀려 잠겼습니다. {secs // 60}분 {secs % 60}초 후 다시 시도하세요")


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(config.SESSION_COOKIE, path="/")
    return {"ok": True}


# ---------------------------------------------------------------- 체험 모드

@app.post("/api/demo")
def demo_start(request: Request, response: Response, db: Session = Depends(get_db)):
    _cleanup_expired_demo(db)
    ip_h = hash_ip(_client_ip(request))
    hour_ago = utcnow() - timedelta(hours=1)
    recent = db.scalar(select(func.count(User.id)).where(
        User.is_demo.is_(True), User.created_ip == ip_h, User.created_at > hour_ago,
    ))
    if recent >= config.DEMO_PER_IP_PER_HOUR:
        raise HTTPException(429, "체험 계정을 너무 많이 만들었습니다. 잠시 후 다시 시도하세요")
    total = db.scalar(select(func.count(User.id)).where(User.is_demo.is_(True), User.created_at > hour_ago))
    if total >= config.DEMO_TOTAL_PER_HOUR:
        raise HTTPException(429, "지금 체험 사용자가 많습니다. 잠시 후 다시 시도하세요")

    user = User(
        name=DEMO_PREFIX + secrets.token_hex(8),
        pin_hash=hash_pin(secrets.token_hex(8)),
        is_demo=True,
        expires_at=utcnow() + timedelta(hours=config.DEMO_HOURS),
        created_ip=ip_h,
    )
    db.add(user)
    db.flush()
    seed.seed_demo(db, user)
    db.commit()
    _set_session(response, request, user)
    return {"ok": True}


@app.post("/api/demo/reset")
def demo_reset(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not user.is_demo:
        raise HTTPException(400, "체험 계정이 아닙니다")
    seed.clear_user_data(db, user.id)
    seed.seed_demo(db, user)
    db.commit()
    return {"ok": True}


@app.get("/api/cron/cleanup")
def cron_cleanup(request: Request, db: Session = Depends(get_db)):
    if config.CRON_SECRET and request.headers.get("authorization") != f"Bearer {config.CRON_SECRET}":
        raise HTTPException(401, "unauthorized")
    return {"deleted": _cleanup_expired_demo(db)}


# ---------------------------------------------------------------- 시작 화면

def _last_record_times(db: Session, task_ids: list[int]) -> dict[int, object]:
    if not task_ids:
        return {}
    rows = db.execute(
        select(Record.task_id, func.max(Record.created_at)).where(Record.task_id.in_(task_ids)).group_by(Record.task_id)
    )
    return {tid: at for tid, at in rows}


def _project_dict(p: Project) -> dict:
    return {"id": p.id, "name": p.name, "vcs_type": p.vcs_type, "active_task_id": p.active_task_id}


@app.get("/api/home")
def home(project_id: int | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    projects = list(db.scalars(select(Project).where(Project.user_id == user.id).order_by(Project.created_at, Project.id)))
    by_id = {p.id: p for p in projects}
    project = by_id.get(project_id) if project_id else None
    if project is None:
        project = by_id.get(user.last_project_id) or (projects[0] if projects else None)
    if project and user.last_project_id != project.id:
        user.last_project_id = project.id
        db.commit()

    tasks, card = [], None
    if project:
        rows = list(db.scalars(select(Task).where(Task.project_id == project.id).order_by(Task.id)))
        last_at = _last_record_times(db, [t.id for t in rows])
        order = {"active": 0, "waiting": 1, "done": 2}
        rows.sort(key=lambda t: (order.get(t.status, 3), t.id))
        tasks = [task_dict(t, last_at.get(t.id)) for t in rows]
        active = next((t for t in rows if t.id == project.active_task_id and t.status == "active"), None)
        if active:
            card = build_card(db, project, active)

    return {
        "user": _user_dict(user),
        "features": _features(user),
        "projects": [_project_dict(p) for p in projects],
        "project": _project_dict(project) if project else None,
        "tasks": tasks,
        "card": card,
    }


# ---------------------------------------------------------------- 프로젝트·작업

class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    ticket_no: str = Field(default="", max_length=60)
    start: bool = True


class TaskPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    ticket_no: str | None = Field(default=None, max_length=60)
    status: str | None = None


@app.post("/api/projects")
def create_project(body: ProjectIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "프로젝트 이름을 입력하세요")
    p = Project(user_id=user.id, name=name)
    db.add(p)
    db.flush()
    user.last_project_id = p.id
    db.commit()
    return _project_dict(p)


@app.patch("/api/projects/{project_id}")
def rename_project(project_id: int, body: ProjectIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _own_project(db, user, project_id)
    p.name = body.name.strip() or p.name
    db.commit()
    return _project_dict(p)


def _activate(db: Session, project: Project, task: Task) -> None:
    if project.active_task_id and project.active_task_id != task.id:
        prev = db.get(Task, project.active_task_id)
        if prev and prev.status == "active":
            prev.status = "waiting"
            prev.updated_at = utcnow()
    task.status = "active"
    task.updated_at = utcnow()
    project.active_task_id = task.id


@app.post("/api/projects/{project_id}/tasks")
def create_task(project_id: int, body: TaskIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _own_project(db, user, project_id)
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "작업 이름을 입력하세요")
    t = Task(project_id=p.id, title=title, ticket_no=body.ticket_no.strip() or None, status="waiting")
    db.add(t)
    db.flush()
    if body.start:
        _activate(db, p, t)
    user.last_project_id = p.id
    db.commit()
    return task_dict(t)


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, body: TaskPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t, p = _own_task(db, user, task_id)
    if body.title is not None and body.title.strip():
        t.title = body.title.strip()
    if body.ticket_no is not None:
        t.ticket_no = body.ticket_no.strip() or None
    if body.status is not None:
        if body.status not in ("active", "waiting", "done"):
            raise HTTPException(400, "잘못된 상태입니다")
        if body.status == "active":
            _activate(db, p, t)
        else:
            t.status = body.status
            if p.active_task_id == t.id:
                p.active_task_id = None
    t.updated_at = utcnow()
    db.commit()
    return task_dict(t)


@app.post("/api/tasks/{task_id}/start")
def start_task(task_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t, p = _own_task(db, user, task_id)
    _activate(db, p, t)
    user.last_project_id = p.id
    db.commit()
    return task_dict(t)


# ---------------------------------------------------------------- 기록

class RecordIn(BaseModel):
    type: str = "manual"
    what: str = Field(default="", max_length=2000)
    where: str = Field(default="", max_length=2000)
    next: str = Field(default="", max_length=2000)
    memo: str = Field(default="", max_length=20000)
    links: list[str] = Field(default_factory=list)
    vcs_raw: str = Field(default="", max_length=20000)


class VcsIn(BaseModel):
    text: str = Field(default="", max_length=20000)


@app.post("/api/vcs/parse")
def parse_vcs(body: VcsIn, user: User = Depends(current_user)):
    return vcs.parse(body.text)


@app.get("/api/tasks/{task_id}/draft")
def task_draft(task_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """멈춤 기록 화면의 미리 채울 값. 직전 기록이 자동 초안이면 초안의 다음엔·메모도 복원한다."""
    t, p = _own_task(db, user, task_id)
    recs = recent_records(db, t.id, 1)
    latest = record_dict(recs[0]) if recs else None
    card = build_card(db, p, t)
    return {
        "project": _project_dict(p),
        "task": task_dict(t, recs[0].created_at if recs else None),
        "latest": latest,
        # 수정 파일·링크는 다음 기록으로 이어진다 (카드와 같은 병합 규칙)
        "carry": {"what": card["what"], "where": card["where"], "links": card["links"], "vcs_raw": card["vcs_raw"]},
    }


@app.get("/api/tasks/{task_id}/records")
def task_records(task_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t, _ = _own_task(db, user, task_id)
    recs = list(db.scalars(
        select(Record).where(Record.task_id == t.id).order_by(Record.created_at.desc(), Record.id.desc())
    ))
    return {"task": task_dict(t), "records": [record_dict(r) for r in recs]}


@app.post("/api/tasks/{task_id}/records")
def save_record(task_id: int, body: RecordIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.type not in ("manual", "auto"):
        raise HTTPException(400, "잘못된 기록 종류입니다")
    t, p = _own_task(db, user, task_id)

    links = [l.strip() for l in body.links if l.strip()][:20]
    what, where, next_ = body.what.strip(), body.where.strip(), body.next.strip()
    memo, vcs_raw = body.memo.strip(), body.vcs_raw.strip()
    h = content_hash(what, where, next_, memo, links, vcs_raw)
    parsed = vcs.parse(vcs_raw) if vcs_raw else {"vcs": "unknown", "branch": None, "files": []}

    latest = db.scalar(
        select(Record).where(Record.task_id == t.id).order_by(Record.created_at.desc(), Record.id.desc()).limit(1)
    )
    now = utcnow()

    if body.type == "auto":
        if latest and latest.content_hash == h:
            return {"saved": False, "reason": "unchanged", "record": record_dict(latest)}
        if latest and latest.type == "auto":
            rec = latest  # 마지막 수동 기록 이후의 자동 초안은 1건만 유지하고 갱신
        else:
            rec = Record(task_id=t.id, type="auto")
            db.add(rec)
    else:
        rec = Record(task_id=t.id, type="manual")
        db.add(rec)

    rec.what, rec.where_text, rec.next_text = what, where, next_
    rec.memo, rec.links, rec.vcs_raw = memo, links, vcs_raw
    rec.vcs_branch, rec.vcs_files = (parsed["branch"] or "")[:200] or None, parsed["files"][:500]
    rec.content_hash = h
    rec.created_at = now

    if parsed["vcs"] in ("git", "svn") and p.vcs_type != parsed["vcs"]:
        p.vcs_type = parsed["vcs"]
    t.updated_at = now
    user.last_project_id = p.id
    db.commit()
    return {"saved": True, "record": record_dict(rec)}


# ---------------------------------------------------------------- 복귀 브리핑

@app.post("/api/tasks/{task_id}/briefing")
def task_briefing(task_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t, p = _own_task(db, user, task_id)
    existing = db.scalar(select(Briefing).where(Briefing.task_id == t.id))
    recs = [record_dict(r) for r in recent_records(db, t.id, briefing_mod.MAX_RECORDS)]
    src = briefing_mod.build_source(p.name, task_label(t), recs)
    h = briefing_mod.source_hash(src)

    def out(b: Briefing, cached: bool, **extra) -> dict:
        return {"summary": b.summary, "model": b.model, "created_at": iso(b.created_at),
                "cached": cached, **extra}

    if user.is_demo:
        # 체험 방문자의 입력으로는 API를 호출하지 않는다 (비용·호출 한도 보호)
        if existing:
            return out(existing, True, demo=True, stale=existing.source_hash != h)
        raise HTTPException(404, "체험 모드에서는 결제시스템 '유지보수 #7' 작업의 미리 만든 요약만 볼 수 있습니다")

    if not config.GEMINI_API_KEY:
        raise HTTPException(404, "요약 기능이 꺼져 있습니다")
    if not recs:
        raise HTTPException(400, "요약할 기록이 없습니다")
    if existing and existing.source_hash == h:
        return out(existing, True)

    # 키 할당량 보호: 사용자별 하루 호출 횟수 제한
    used = db.scalar(select(func.count(BriefingCall.id)).where(
        BriefingCall.user_id == user.id, BriefingCall.created_at > utcnow() - timedelta(days=1)))
    if used >= config.BRIEFING_PER_USER_PER_DAY:
        raise HTTPException(429, f"요약은 하루 {config.BRIEFING_PER_USER_PER_DAY}회까지 쓸 수 있습니다")
    db.add(BriefingCall(user_id=user.id))
    db.commit()  # 외부 API를 기다리는 동안 DB 트랜잭션(락)을 잡고 있지 않도록 먼저 끝낸다

    try:
        summary, used_model = briefing_mod.call_gemini(src)
    except briefing_mod.BriefingError as e:
        raise HTTPException(502, f"요약을 가져오지 못했습니다 ({e})")

    for _ in range(2):
        b = db.scalar(select(Briefing).where(Briefing.task_id == t.id))
        if b:
            b.summary, b.source_hash, b.model, b.created_at = summary, h, used_model, utcnow()
        else:
            b = Briefing(task_id=t.id, summary=summary, source_hash=h, model=used_model)
            db.add(b)
        try:
            db.commit()
            break
        except IntegrityError:  # 같은 작업의 요약이 동시에 저장됨 → 다시 읽어서 갱신
            db.rollback()
    return out(b, False)


# ---------------------------------------------------------------- 정적 파일 (로컬 개발용; Vercel은 public/을 CDN으로 서빙)

_PUBLIC = Path(__file__).resolve().parent.parent / "public"
if _PUBLIC.is_dir():
    app.mount("/", StaticFiles(directory=str(_PUBLIC), html=True), name="public")
