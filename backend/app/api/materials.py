"""素材库路由：卡片 CRUD + 资信文件上传入库 + 检索。"""
import re
import time
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.storage import sha256_of, storage
from app.models.file_object import FileObject
from app.models.material import MATERIAL_TYPES, Material
from app.models.user import User
from app.services.ingest_service import run_ingest

router = APIRouter(prefix="/api/materials", tags=["materials"])


class MaterialOut(BaseModel):
    id: int
    type: str
    name: str
    summary: str
    qual_extra: dict
    tags: str
    source: str
    updated_at: str


class MaterialIn(BaseModel):
    type: str
    name: str
    summary: str = ""
    qual_extra: dict = {}
    tags: str = ""


class IngestResultOut(BaseModel):
    stats: dict
    gaps: list[str]
    source: str


def _to_out(m: Material) -> MaterialOut:
    return MaterialOut(
        id=m.id, type=m.type, name=m.name, summary=m.summary,
        qual_extra=m.qual_extra or {}, tags=m.tags, source=m.source,
        updated_at=m.updated_at.isoformat() if m.updated_at else "",
    )


@router.get("", response_model=list[MaterialOut])
def list_materials(
    type: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(Material)
    if type:
        query = query.filter(Material.type == type)
    rows = query.order_by(Material.updated_at.desc()).all()
    if q:
        rows = [m for m in rows if q in m.name or q in m.summary or q in m.tags]
    return [_to_out(m) for m in rows]


@router.post("", response_model=MaterialOut)
def create_material(body: MaterialIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if body.type not in MATERIAL_TYPES:
        raise HTTPException(status_code=400, detail=f"type 须为 {'/'.join(MATERIAL_TYPES)}")
    m = Material(**body.model_dump())
    db.add(m)
    db.commit()
    db.refresh(m)
    return _to_out(m)


@router.put("/{material_id}", response_model=MaterialOut)
def update_material(
    material_id: int, body: MaterialIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    m = db.get(Material, material_id)
    if m is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    for k, v in body.model_dump().items():
        setattr(m, k, v)
    db.commit()
    db.refresh(m)
    return _to_out(m)


@router.delete("/{material_id}")
def delete_material(material_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    m = db.get(Material, material_id)
    if m is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    db.delete(m)
    db.commit()
    return {"deleted": material_id}


@router.post("/ingest", response_model=IngestResultOut)
async def ingest(file: UploadFile, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """上传资信文件（docx）→ 解析 → 素材卡入库。"""
    original = file.filename or "material"
    if PurePosixPath(original).suffix.lower() != ".docx":
        raise HTTPException(status_code=400, detail="素材入库当前仅支持 .docx 资信文件")
    data = await file.read()
    safe_name = re.sub(r"[^\w.\-一-鿿]", "_", original)
    rel = f"materials/{int(time.time() * 1000)}-{safe_name}"
    storage.put(rel, data)
    db.add(FileObject(
        bucket="material", relative_path=rel, original_name=original,
        mime=file.content_type or "application/octet-stream",
        size=len(data), sha256=sha256_of(data),
    ))
    db.commit()
    return IngestResultOut(**run_ingest(rel, original))
