"""大纲表（Q27 定稿：草稿可编辑 + 确认即快照，AI 原始建议与人工定稿可对比）。

- outline_drafts：LLM 生成后用户直接编辑，随改随存（jsonb 章节树）
- outline_snapshots：点确认时整体复制，version 递增；确认后再改 = 在快照上解锁编辑，保存 version+1
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, TypeDecorator

from app.core.database import Base


class JSONBCompat(TypeDecorator):
    """PostgreSQL 用 JSONB，sqlite（本地演示）退化为 JSON。"""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class OutlineDraft(Base):
    __tablename__ = "outline_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), unique=True, index=True)
    tree: Mapped[dict] = mapped_column(JSONBCompat)  # {nodes: [{id,title,target_words,scoring_keys[],children[]}]}
    ai_raw_tree: Mapped[dict] = mapped_column(JSONBCompat)  # LLM 原始输出，永不改，供对比
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OutlineSnapshot(Base):
    __tablename__ = "outline_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    version: Mapped[int] = mapped_column()
    tree: Mapped[dict] = mapped_column(JSONBCompat)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
