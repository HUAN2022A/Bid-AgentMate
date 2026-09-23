"""标书工作台编排：一次解析 = 招标侧 LLM 拆解（复用 analyze 核心，不生成大纲）+ 投标侧章节树。

状态边：wb_parsing → wb_outline_pending | wb_parse_failed（重新上传/重试回 created/wb_parse_failed）。
"""
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.project import Project
from app.models.tender_file import TenderFile
from app.services.analyze_service import _analyze_tender_core
from app.services.bid_parse_service import run_bid_parse


def run_workbench_parse(project_id: int) -> None:
    """Celery 任务或同步调用共用：两步解析，全部成功才推进 wb_outline_pending。"""
    db: Session = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project is None or project.mode != "workbench" or project.state != "wb_parsing":
            return
        tenders = (
            db.query(TenderFile)
            .filter(TenderFile.project_id == project_id)
            .order_by(TenderFile.id)
            .all()
        )
        if not any(t.role == "main" for t in tenders):
            _fail(db, project, "未找到招标文件正文（role=main）")
            return
        if not any(t.role == "bid" for t in tenders):
            _fail(db, project, "未上传投标文件（role=bid）")
            return

        try:
            soft_warn = _analyze_tender_core(db, project, tenders)
            db.commit()  # 招标侧结果先落库释放写锁，再跑投标解析
            run_bid_parse(db, project)
            project.state = "wb_outline_pending"
            project.parse_error = soft_warn
            db.commit()
        except Exception as e:  # noqa: BLE001 两步任何失败统一落 wb_parse_failed
            db.rollback()
            _fail(db, project, f"{type(e).__name__}: {e}")
    finally:
        db.close()


def _fail(db: Session, project: Project, msg: str) -> None:
    project.state = "wb_parse_failed"
    project.parse_error = msg[:2000]
    db.commit()


def dispatch_workbench_parse(project_id: int) -> str:
    if settings.sync_tasks:
        run_workbench_parse(project_id)
        return "sync"
    from app.worker import celery_app

    celery_app.send_task("app.worker.workbench_parse", args=[project_id])
    return "celery"
