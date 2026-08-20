"""项目路由：创建/列表/详情/上传招标文件/解析状态/下载提取全文。

阶段 1 闭环：上传 → parsing → outline_pending（或 parse_failed）→ 下载 extracted.txt。
"""
import re
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.core.storage import sha256_of, storage
from app.models.file_object import FileObject
from app.models.project import Project
from app.models.tender_file import TenderFile
from app.models.user import User
from app.services.analyze_service import dispatch_analyze

router = APIRouter(prefix="/api/projects", tags=["projects"])

ALLOWED_EXT = {".pdf": "pdf", ".docx": "docx"}


class ProjectCreate(BaseModel):
    name: str
    tender_no: str = ""


class ProjectOut(BaseModel):
    id: int
    name: str
    tender_no: str
    state: str
    parse_error: str
    outline_version: int
    created_at: str

    model_config = {"from_attributes": True}


class TenderFileOut(BaseModel):
    id: int
    file_type: str
    original_name: str
    size: int
    extract_stats: str
    extracted: bool


def _to_out(p: Project) -> ProjectOut:
    return ProjectOut(
        id=p.id,
        name=p.name,
        tender_no=p.tender_no,
        state=p.state,
        parse_error=p.parse_error,
        outline_version=p.outline_version,
        created_at=p.created_at.isoformat() if p.created_at else "",
    )


@router.post("", response_model=ProjectOut)
def create_project(
    body: ProjectCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    p = Project(name=body.name, tender_no=body.tender_no, created_by=user.id)
    db.add(p)
    db.commit()
    db.refresh(p)
    return _to_out(p)


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [_to_out(p) for p in db.query(Project).order_by(Project.id.desc()).all()]


def _get_project(db: Session, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return p


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return _to_out(_get_project(db, project_id))


@router.post("/{project_id}/tender", response_model=ProjectOut)
async def upload_tender(
    project_id: int,
    file: UploadFile,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """上传招标文件并触发解析。parse_failed 状态下重新上传即回退到 parsing（Q21）。"""
    p = _get_project(db, project_id)
    if p.state not in ("created", "parse_failed"):
        raise HTTPException(status_code=409, detail=f"当前状态 {p.state} 不允许上传招标文件")

    original = file.filename or "tender"
    ext = PurePosixPath(original).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="仅支持 .pdf / .docx 招标文件")

    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"文件超过 {settings.max_upload_mb}MB 限制")

    safe_name = re.sub(r"[^\w.\-一-鿿]", "_", original)
    rel = f"projects/{project_id}/tender/{safe_name}"
    storage.put(rel, data)

    fo = FileObject(
        bucket="project",
        relative_path=rel,
        original_name=original,
        mime=file.content_type or "application/octet-stream",
        size=len(data),
        sha256=sha256_of(data),
    )
    db.add(fo)
    db.flush()

    tender = TenderFile(project_id=project_id, file_object_id=fo.id, file_type=ALLOWED_EXT[ext])
    db.add(tender)

    p.state = "parsing"
    p.parse_error = ""
    db.commit()

    mode = dispatch_analyze(project_id)
    db.refresh(p)
    out = _to_out(p)
    if mode == "sync":  # 同步模式下状态已终态，直接返回最新
        db.refresh(p)
        out = _to_out(p)
    return out


@router.get("/{project_id}/tender", response_model=list[TenderFileOut])
def list_tender_files(
    project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    _get_project(db, project_id)
    rows = (
        db.query(TenderFile, FileObject)
        .join(FileObject, TenderFile.file_object_id == FileObject.id)
        .filter(TenderFile.project_id == project_id)
        .order_by(TenderFile.id.desc())
        .all()
    )
    return [
        TenderFileOut(
            id=t.id,
            file_type=t.file_type,
            original_name=fo.original_name,
            size=fo.size,
            extract_stats=t.extract_stats,
            extracted=bool(t.extracted_text_path),
        )
        for t, fo in rows
    ]


@router.get("/{project_id}/tender/extracted")
def download_extracted(
    project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """下载提取全文（阶段 1 的"分析报告"占位；阶段 2 换成 LLM 解析报告 docx）。"""
    _get_project(db, project_id)
    tender = (
        db.query(TenderFile)
        .filter(TenderFile.project_id == project_id)
        .order_by(TenderFile.id.desc())
        .first()
    )
    if tender is None or not tender.extracted_text_path:
        raise HTTPException(status_code=404, detail="尚无提取结果")
    path = storage.abspath(tender.extracted_text_path)
    return FileResponse(path, filename="招标文件提取全文.txt", media_type="text/plain; charset=utf-8")
