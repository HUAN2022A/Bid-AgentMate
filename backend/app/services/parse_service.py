"""招标文件解析服务（阶段 1：提取全文；阶段 2 将在此叠加 LLM 评分点拆解）。

任务派发遵循 Q22：状态机自管，Celery 只当执行器；sync_tasks 模式下同步执行便于本地演示。
"""
import json
import sys
from pathlib import Path

from sqlalchemy.orm import Session

# scripts/ 与 app/ 同级，直接挂进 sys.path 复用移植脚本
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from scripts.extract_docx import extract_docx_lines  # noqa: E402
from scripts.extract_pdf import extract_pdf_lines  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.storage import storage  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.models.tender_file import TenderFile  # noqa: E402


def run_parse(project_id: int) -> None:
    """解析入口（Celery 任务或同步调用共用）。状态边：parsing → outline_pending | parse_failed。"""
    db: Session = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project is None or project.state != "parsing":
            return
        tender = (
            db.query(TenderFile)
            .filter(TenderFile.project_id == project_id)
            .order_by(TenderFile.id.desc())
            .first()
        )
        if tender is None:
            project.state = "parse_failed"
            project.parse_error = "未找到招标文件"
            db.commit()
            return

        try:
            src = storage.abspath(
                _file_path_of(db, tender.file_object_id)
            )
            if tender.file_type == "pdf":
                lines, stats = extract_pdf_lines(str(src))
            else:
                lines, stats = extract_docx_lines(str(src))

            text_rel = f"projects/{project_id}/tender/extracted.txt"
            storage.put(text_rel, "\n".join(lines).encode("utf-8"))
            tender.extracted_text_path = text_rel
            tender.extract_stats = json.dumps(stats, ensure_ascii=False)

            # 阶段 1 闭环：解析 = 提取成功即进入大纲待确认（LLM 拆解在阶段 2 插入此处）
            project.state = "outline_pending"
            project.parse_error = ""
            if stats.get("maybe_scanned"):
                project.parse_error = "WARN: 平均每页字符偏少，可能是扫描件，请人工核对提取质量"
            db.commit()
        except Exception as e:  # 解析失败允许重新上传（Q21 唯一回退边）
            project.state = "parse_failed"
            project.parse_error = f"{type(e).__name__}: {e}"[:2000]
            db.commit()
    finally:
        db.close()


def _file_path_of(db: Session, file_object_id: int) -> str:
    from app.models.file_object import FileObject

    fo = db.get(FileObject, file_object_id)
    if fo is None:
        raise FileNotFoundError(f"file_object {file_object_id} 不存在")
    return fo.relative_path


def dispatch_parse(project_id: int) -> str:
    """按配置走 Celery 或同步执行，返回执行模式（便于前端提示）。"""
    if settings.sync_tasks:
        run_parse(project_id)
        return "sync"
    from app.worker import celery_app

    celery_app.send_task("app.worker.parse_tender", args=[project_id])
    return "celery"
