"""招标文件表：原始文件引用 + 提取全文 + 解析结果。"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TenderFile(Base):
    __tablename__ = "tender_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    file_object_id: Mapped[int] = mapped_column(ForeignKey("file_objects.id"))  # 原始文件
    file_type: Mapped[str] = mapped_column(String(16))  # pdf / docx
    extracted_text_path: Mapped[str] = mapped_column(String(512), default="")  # 提取全文 txt 相对路径
    extract_stats: Mapped[str] = mapped_column(String(512), default="")  # 页数/表格数/字符数摘要
    analysis_yaml_path: Mapped[str] = mapped_column(String(512), default="")  # 阶段 2：LLM 解析结果
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
