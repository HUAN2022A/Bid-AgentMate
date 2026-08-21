"""交付路由：自查 + 导出（状态机 checking → exported）。"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.storage import storage
from app.models.project import Project
from app.models.user import User
from app.services.check_service import run_check
from app.services.export_service import run_export

router = APIRouter(prefix="/api/projects/{project_id}", tags=["delivery"])


class CheckSummaryOut(BaseModel):
    report_path: str
    tech_items: int
    covered: int
    star_reqs: int
    star_hit: int
    pending_gaps: int
    price_hits: int


class ExportSummaryOut(BaseModel):
    export_path: str
    chapters: int
    total_words: int
    pending_gaps: int
    exported_at: str


def _get_project(db: Session, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return p


@router.post("/check", response_model=CheckSummaryOut)
def check(project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _get_project(db, project_id)
    result = run_check(project_id)
    if "error" in result:
        raise HTTPException(status_code=409, detail=result["error"])
    return CheckSummaryOut(**result)


@router.get("/check/report")
def download_check_report(
    project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    _get_project(db, project_id)
    rel = f"projects/{project_id}/export/check-report.md"
    if not storage.exists(rel):
        raise HTTPException(status_code=404, detail="尚无自查报告")
    return FileResponse(storage.abspath(rel), filename="自查报告.md", media_type="text/markdown; charset=utf-8")


@router.post("/export", response_model=ExportSummaryOut)
def export(project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _get_project(db, project_id)
    result = run_export(project_id)
    if "error" in result:
        raise HTTPException(status_code=409, detail=result["error"])
    return ExportSummaryOut(**result)


@router.get("/export/docx")
def download_docx(
    project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    _get_project(db, project_id)
    rel = f"projects/{project_id}/export/技术文件.docx"
    if not storage.exists(rel):
        raise HTTPException(status_code=404, detail="尚无导出文件")
    return FileResponse(
        storage.abspath(rel),
        filename="技术文件.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
