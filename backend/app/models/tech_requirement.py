"""技术需求表（★/▲ 硬指标）。"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TechRequirementRow(Base):
    __tablename__ = "tech_requirements"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    req_key: Mapped[str] = mapped_column(String(16))  # T1/T2…
    star: Mapped[bool] = mapped_column(Boolean, default=False)
    requirement_original: Mapped[str] = mapped_column(Text)
    location: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
