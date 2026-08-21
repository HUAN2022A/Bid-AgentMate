"""素材卡 + 项目引用快照（Q5/Q9 定稿）。

- materials：全局素材库，固定核心字段（type/name/sub_type/source/summary）+ qual_extra jsonb 扩展
- material_snapshots：项目引用时的卡片内容副本——卡更新不影响已完成标书（Git 引用 commit 思路）
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.outline import JSONBCompat

# 素材类型：case 案例 / person 人员 / credential 资质获奖 / ip 知识产权 / capability 研发能力
MATERIAL_TYPES = ["case", "person", "credential", "ip", "capability"]


class Material(Base):
    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")  # 一段话摘要（起草注入用）
    qual_extra: Mapped[dict] = mapped_column(JSONBCompat, default=dict)  # 资格关键字段（Q9 扩展）
    tags: Mapped[str] = mapped_column(String(512), default="")  # 逗号分隔，检索用
    source: Mapped[str] = mapped_column(String(256), default="")  # 来源文件，可追溯
    tenant_id: Mapped[int] = mapped_column(default=1)  # Q2 预留
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MaterialSnapshot(Base):
    """项目引用快照（Q5）：起草引用素材时存副本，卡更新不影响已完成标书。"""

    __tablename__ = "material_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    content: Mapped[dict] = mapped_column(JSONBCompat)  # 引用时刻的卡片完整内容
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
