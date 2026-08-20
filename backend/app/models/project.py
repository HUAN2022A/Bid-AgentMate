"""项目表：状态机线性主状态（Q21 定稿）。"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# Q21：线性主状态集，唯一回退边 parse_failed → parsing（重新上传触发）
PROJECT_STATES = [
    "created",
    "parsing",
    "parse_failed",
    "outline_pending",
    "outline_confirmed",
    "drafting",
    "draft_done",
    "checking",
    "exported",
]


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    tender_no: Mapped[str] = mapped_column(String(128), default="")  # 招标编号
    state: Mapped[str] = mapped_column(String(32), default="created", index=True)
    parse_error: Mapped[str] = mapped_column(String(2000), default="")
    outline_version: Mapped[int] = mapped_column(default=0)  # Q21：大纲变更走版本递增，不进主状态
    tenant_id: Mapped[int] = mapped_column(default=1)  # Q2：预留不开通
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
