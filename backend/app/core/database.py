"""数据库：SQLAlchemy 2.0 engine/session（Q15 定稿）。"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# sqlite 本地开发：长事务（LLM 解析期间 session 不关）会持读锁阻塞并发写
# （润色采纳/copilot 落库报 database is locked）。WAL 允许读写并存，busy_timeout 让写排队等待。
if engine.dialect.name == "sqlite":

    @event.listens_for(engine, "connect")
    def _sqlite_pragma(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
