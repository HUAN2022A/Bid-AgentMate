"""项目路由：创建/列表/详情/上传招标文件/触发解析/解析状态/下载提取全文。

多文件（2026-08-21 定稿）：上传与解析分离——created 态可反复上传多份（main/spec/attachment），
POST /{id}/parse 一次性触发全量解析。
"""
import re
import time
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
from app.models.tender_file import TENDER_ROLES, TenderFile
from app.models.user import User
from app.services.analyze_service import dispatch_analyze

router = APIRouter(prefix="/api/projects", tags=["projects"])

ALLOWED_EXT = {".pdf": "pdf", ".docx": "docx"}

ROLE_LABELS = {"main": "招标文件", "spec": "技术规范书", "attachment": "附件"}


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
    role: str
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


@router.post("/{project_id}/tender", response_model=TenderFileOut)
async def upload_tender(
    project_id: int,
    file: UploadFile,
    role: str = "main",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """上传一份招标文件（仅存文件不触发解析）。created/parse_failed 态可反复上传。"""
    p = _get_project(db, project_id)
    if p.state not in ("created", "parse_failed"):
        raise HTTPException(status_code=409, detail=f"当前状态 {p.state} 不允许上传招标文件")
    if role not in TENDER_ROLES:
        raise HTTPException(status_code=400, detail=f"role 须为 {'/'.join(TENDER_ROLES)}")

    original = file.filename or "tender"
    ext = PurePosixPath(original).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="仅支持 .pdf / .docx 招标文件")

    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"文件超过 {settings.max_upload_mb}MB 限制")

    safe_name = re.sub(r"[^\w.\-一-鿿]", "_", original)
    # 重传/同名文件防 UNIQUE 冲突：相对路径带毫秒时间戳
    rel = f"projects/{project_id}/tender/{int(time.time() * 1000)}-{safe_name}"
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

    tender = TenderFile(
        project_id=project_id, file_object_id=fo.id, role=role, file_type=ALLOWED_EXT[ext]
    )
    db.add(tender)
    if p.state == "parse_failed":  # 回退到 created 等重新触发解析
        p.state = "created"
        p.parse_error = ""
    db.commit()
    db.refresh(tender)
    return TenderFileOut(
        id=tender.id,
        role=tender.role,
        file_type=tender.file_type,
        original_name=original,
        size=len(data),
        extract_stats="",
        extracted=False,
    )


@router.post("/{project_id}/parse", response_model=ProjectOut)
def trigger_parse(
    project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """一次性触发全量解析：所有已上传文件按角色组装上下文喂 LLM。

    outline_pending 态允许重跑（重新生成解析结果与大纲草稿，覆盖式）。
    """
    p = _get_project(db, project_id)
    if p.state not in ("created", "parse_failed", "outline_pending"):
        raise HTTPException(status_code=409, detail=f"当前状态 {p.state} 不允许触发解析")
    tenders = db.query(TenderFile).filter(TenderFile.project_id == project_id).all()
    if not any(t.role == "main" for t in tenders):
        raise HTTPException(status_code=400, detail="请先上传招标文件正文（role=main）")

    p.state = "parsing"
    p.parse_error = ""
    db.commit()

    mode = dispatch_analyze(project_id)
    db.refresh(p)
    return _to_out(p)


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
            role=t.role,
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
    project_id: int,
    role: str = "main",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """下载提取全文（按角色取最新一份）。"""
    _get_project(db, project_id)
    tender = (
        db.query(TenderFile)
        .filter(TenderFile.project_id == project_id, TenderFile.role == role)
        .order_by(TenderFile.id.desc())
        .first()
    )
    if tender is None or not tender.extracted_text_path:
        raise HTTPException(status_code=404, detail="尚无提取结果")
    path = storage.abspath(tender.extracted_text_path)
    return FileResponse(path, filename=f"{ROLE_LABELS.get(role, role)}提取全文.txt", media_type="text/plain; charset=utf-8")
