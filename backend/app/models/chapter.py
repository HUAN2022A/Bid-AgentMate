"""章节正文 + 起草任务 + 版本历史（Q22/Q24 定稿）。

- chapters：每个叶章节一行，当前正文 = 最新版本行（不双写）
- chapter_tasks：章节级任务记录，dispatcher 扫"待起草"行逐个派发，Celery 只当执行器
- chapter_versions：全量快照，source 区分 人工/AI整章/AI段落
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# 章节状态机：pending → drafting → draft_done | draft_failed；人工编辑后 edited
CHAPTER_STATES = ["pending", "drafting", "draft_done", "draft_failed", "edited"]

# 版本来源（Q24）
VERSION_SOURCES = ["human", "ai_chapter", "ai_paragraph"]


class Chapter(Base):
    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    chapter_key: Mapped[str] = mapped_column(String(32))  # 大纲章节 id，如 4.5.2
    title: Mapped[str] = mapped_column(String(256))
    target_words: Mapped[int] = mapped_column(Integer, default=2000)
    scoring_keys: Mapped[str] = mapped_column(String(256), default="")  # 逗号分隔 S4,S5
    sort_order: Mapped[int] = mapped_column(Integer, default=0)  # 大纲叶序
    state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    draft_error: Mapped[str] = mapped_column(String(2000), default="")
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)  # Q21：大纲变更标志
    outline_version: Mapped[int] = mapped_column(Integer, default=1)  # 起草时的大纲版本
    locked_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)  # Q4：章节锁
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChapterTask(Base):
    """章节级任务记录（Q22）：dispatcher 扫 pending 行逐个派发，状态机自管。"""

    __tablename__ = "chapter_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    state: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending/running/done/failed
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    celery_task_id: Mapped[str] = mapped_column(String(64), default="")
    error: Mapped[str] = mapped_column(String(2000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)


class ChapterVersion(Base):
    """全量快照（Q24）：当前正文永远读最新版本行。"""

    __tablename__ = "chapter_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    content_md: Mapped[str] = mapped_column(Text)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(16))  # human / ai_chapter / ai_paragraph
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
