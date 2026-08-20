"""文件对象表（Q25 定稿）：数据库只存相对路径 + 元信息，二进制落本地盘。"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FileObject(Base):
    __tablename__ = "file_objects"

    id: Mapped[int] = mapped_column(primary_key=True)
    bucket: Mapped[str] = mapped_column(String(32), index=True)  # project / material
    relative_path: Mapped[str] = mapped_column(String(512), unique=True)  # posix 相对路径
    original_name: Mapped[str] = mapped_column(String(256))
    mime: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)  # 去重 + 完整性校验
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
