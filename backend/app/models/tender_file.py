"""招标文件表：原始文件引用 + 提取全文 + 解析结果。

多文件（2026-08-21 定稿）：一个项目可传多份，role 区分角色——
main 招标文件正文（评分/废标/商务）、spec 技术规范书（★参数主来源）、attachment 其他附件。
上传与解析分离：created 态可反复上传，点"开始解析"才触发 run_analyze。
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

TENDER_ROLES = ["main", "spec", "attachment"]


class TenderFile(Base):
    __tablename__ = "tender_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    file_object_id: Mapped[int] = mapped_column(ForeignKey("file_objects.id"))  # 原始文件
    role: Mapped[str] = mapped_column(String(16), default="main", index=True)  # main/spec/attachment
    file_type: Mapped[str] = mapped_column(String(16))  # pdf / docx
    extracted_text_path: Mapped[str] = mapped_column(String(512), default="")  # 提取全文 txt 相对路径
    extract_stats: Mapped[str] = mapped_column(String(512), default="")  # 页数/表格数/字符数摘要
    analysis_yaml_path: Mapped[str] = mapped_column(String(512), default="")  # 仅 main 行：LLM 解析结果快照
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
