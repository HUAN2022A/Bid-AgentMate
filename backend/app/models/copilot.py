"""Copilot 动作日志（章节内段落级 AI 动作）。

生成成功即落行（applied=False 就是"放弃"信号），前端应用时打 applied。
应用率是 Copilot 的北极星指标；input/output 全文留存是采纳挖掘的原料
（高采纳段落 → few-shot，高删除模式 → prompt 负面约束）。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CopilotAction(Base):
    __tablename__ = "copilot_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(16), index=True)
    instruction: Mapped[str] = mapped_column(String(500), default="")
    scoring_key: Mapped[str] = mapped_column(String(32), default="")
    input_md: Mapped[str] = mapped_column(Text, default="")
    output_md: Mapped[str] = mapped_column(Text, default="")
    warnings: Mapped[str] = mapped_column(String(500), default="")  # 软校验标签，分号分隔
    applied: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
