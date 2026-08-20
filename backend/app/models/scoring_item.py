"""评分点表（category 分卷标签：技术|商务|价格|资质|其他）。"""
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ScoringItemRow(Base):
    __tablename__ = "scoring_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    item_key: Mapped[str] = mapped_column(String(16))  # S1/S2…
    category: Mapped[str] = mapped_column(String(8), index=True)
    item: Mapped[str] = mapped_column(String(256))
    score: Mapped[float] = mapped_column(Float)
    criteria_original: Mapped[str] = mapped_column(Text)  # 逐字原文
    location: Mapped[str] = mapped_column(String(256), default="")
    response_hint: Mapped[str] = mapped_column(String(512), default="")
    note: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
