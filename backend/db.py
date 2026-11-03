"""DB 접근 계층. SQLAlchemy로 SQLite(로컬)와 PostgreSQL(배포)을 같은 코드로 다룬다."""
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import NullPool

from .config import DATABASE_URL, IS_VERCEL


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


_engine_kwargs: dict = {}
if DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
elif IS_VERCEL:
    # 서버리스: 인스턴스마다 커넥션 풀을 들고 있지 않도록 (Supabase pooler 사용 권장)
    _engine_kwargs["poolclass"] = NullPool
else:
    _engine_kwargs["pool_pre_ping"] = True

engine = create_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    # SQLite가 삭제된 id를 재사용하면 옛 세션 쿠키가 새 계정으로 로그인되므로 막는다
    __table_args__ = {"sqlite_autoincrement": True}
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    pin_hash: Mapped[str] = mapped_column(String(255))
    # 순환 FK를 피하려고 제약 없이 정수로만 둔다 (앱에서 user_id 조건으로 검증)
    last_project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_ip: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    vcs_type: Mapped[str] = mapped_column(String(10), default="unknown")  # git / svn / unknown
    active_task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    ticket_no: Mapped[str | None] = mapped_column(String(60), nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="waiting")  # active / waiting / done
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Record(Base):
    __tablename__ = "records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(10))  # manual / auto
    what: Mapped[str] = mapped_column(Text, default="")
    where_text: Mapped[str] = mapped_column("where_text", Text, default="")
    next_text: Mapped[str] = mapped_column("next_text", Text, default="")
    memo: Mapped[str] = mapped_column(Text, default="")
    links: Mapped[list] = mapped_column(JSON, default=list)
    vcs_raw: Mapped[str] = mapped_column(Text, default="")
    vcs_branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    vcs_files: Mapped[list] = mapped_column(JSON, default=list)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Briefing(Base):
    __tablename__ = "briefings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), unique=True)
    summary: Mapped[str] = mapped_column(Text)
    source_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Suggestion(Base):
    """멈춤 기록 AI 추천 캐시 (작업당 1건, 입력이 같으면 다시 호출하지 않음)."""
    __tablename__ = "suggestions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), unique=True)
    payload: Mapped[dict] = mapped_column(JSON)
    source_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BriefingCall(Base):
    """AI(복귀 브리핑·멈춤 기록 추천) 호출 기록 (사용자별 하루 한도 계산용)."""
    __tablename__ = "briefing_calls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


_initialized = False


def init_db() -> None:
    global _initialized
    if not _initialized:
        Base.metadata.create_all(engine)
        if engine.dialect.name == "postgresql":
            # Supabase는 public 스키마를 REST API(anon 키)로 노출하므로 RLS를 켜서 막는다.
            # 앱은 테이블 소유자로 접속하므로 RLS의 영향을 받지 않는다.
            # ALTER는 테이블 전체 락을 잡으므로, 아직 꺼진 테이블에만 한 번 실행한다
            names = [t.name for t in Base.metadata.sorted_tables]
            with engine.begin() as conn:
                off = conn.execute(text(
                    "SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace "
                    "AND relkind = 'r' AND NOT relrowsecurity AND relname = ANY(:names)"
                ), {"names": names}).scalars().all()
                for name in off:
                    conn.execute(text(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY'))
        _initialized = True
