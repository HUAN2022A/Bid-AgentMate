"""大纲路由（Q27）：草稿读取/保存 + 确认快照 + 评分点清单（供挂接展示）。

工作台扩展（doc_kind）：kind=tender 为起草大纲（原有行为）；kind=bid 为投标目录树——
bid 树叶节点携带 content_md，确认时物化 chapters + chapter_versions(source='imported')。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.outline import OutlineDraft, OutlineSnapshot
from app.models.project import Project
from app.models.scoring_item import ScoringItemRow
from app.models.tech_requirement import TechRequirementRow
from app.models.user import User
from app.schemas.bid_outline import BidOutlineTree
from app.schemas.outline import OutlineTree
from app.services.bid_parse_service import materialize_bid_chapters

router = APIRouter(prefix="/api/projects/{project_id}", tags=["outline"])


class ScoringItemOut(BaseModel):
    item_key: str
    category: str
    item: str
    score: float
    criteria_original: str
    location: str
    response_hint: str


class TechRequirementOut(BaseModel):
    req_key: str
    star: bool
    requirement_original: str
    location: str


class AnalysisOut(BaseModel):
    scoring_items: list[ScoringItemOut]
    tech_requirements: list[TechRequirementOut]


class OutlineDraftOut(BaseModel):
    tree: dict
    ai_raw_tree: dict
    updated_at: str


class OutlineSaveIn(BaseModel):
    tree: dict


def _get_project(db: Session, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return p


@router.get("/analysis", response_model=AnalysisOut)
def get_analysis(
    project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    _get_project(db, project_id)
    items = (
        db.query(ScoringItemRow)
        .filter(ScoringItemRow.project_id == project_id)
        .order_by(ScoringItemRow.id)
        .all()
    )
    reqs = (
        db.query(TechRequirementRow)
        .filter(TechRequirementRow.project_id == project_id)
        .order_by(TechRequirementRow.id)
        .all()
    )
    return AnalysisOut(
        scoring_items=[ScoringItemOut(**{k: getattr(i, k) for k in ScoringItemOut.model_fields}) for i in items],
        tech_requirements=[
            TechRequirementOut(**{k: getattr(r, k) for k in TechRequirementOut.model_fields}) for r in reqs
        ],
    )


def _get_draft(db: Session, project_id: int, kind: str) -> OutlineDraft:
    return (
        db.query(OutlineDraft)
        .filter(OutlineDraft.project_id == project_id, OutlineDraft.doc_kind == kind)
        .first()
    )


@router.get("/outline", response_model=OutlineDraftOut)
def get_outline(
    project_id: int,
    kind: str = Query("tender", description="tender 起草大纲 | bid 投标目录树"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_project(db, project_id)
    if kind not in ("tender", "bid"):
        raise HTTPException(status_code=400, detail="kind 须为 tender/bid")
    draft = _get_draft(db, project_id, kind)
    if draft is None:
        raise HTTPException(status_code=404, detail="尚无大纲草稿（先上传文件完成解析）")
    return OutlineDraftOut(
        tree=draft.tree,
        ai_raw_tree=draft.ai_raw_tree,
        updated_at=draft.updated_at.isoformat() if draft.updated_at else "",
    )


@router.put("/outline", response_model=OutlineDraftOut)
def save_outline(
    project_id: int,
    body: OutlineSaveIn,
    kind: str = Query("tender"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """保存草稿（随改随存）。tender：outline_pending 可编辑；bid：wb_outline_pending 可编辑。"""
    p = _get_project(db, project_id)
    if kind not in ("tender", "bid"):
        raise HTTPException(status_code=400, detail="kind 须为 tender/bid")
    editable_state = "outline_pending" if kind == "tender" else "wb_outline_pending"
    if p.state != editable_state:
        raise HTTPException(status_code=409, detail=f"当前状态 {p.state} 不允许编辑该草稿")
    # 契约校验：树结构不合法直接 422
    if kind == "tender":
        OutlineTree.model_validate(body.tree)
    else:
        BidOutlineTree.model_validate(body.tree)
    draft = _get_draft(db, project_id, kind)
    if draft is None:
        raise HTTPException(status_code=404, detail="尚无大纲草稿")
    draft.tree = body.tree
    db.commit()
    db.refresh(draft)
    return OutlineDraftOut(
        tree=draft.tree,
        ai_raw_tree=draft.ai_raw_tree,
        updated_at=draft.updated_at.isoformat() if draft.updated_at else "",
    )


@router.post("/outline/confirm")
def confirm_outline(
    project_id: int,
    kind: str = Query("tender"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """确认：草稿整体快照（version 递增）。tender → outline_confirmed（Q27/Q21）；
    bid → 物化 chapters+versions(source='imported') 并推进 wb_ready。"""
    p = _get_project(db, project_id)
    if kind not in ("tender", "bid"):
        raise HTTPException(status_code=400, detail="kind 须为 tender/bid")
    confirm_state = "outline_pending" if kind == "tender" else "wb_outline_pending"
    if p.state != confirm_state:
        raise HTTPException(status_code=409, detail=f"当前状态 {p.state} 不允许确认")
    draft = _get_draft(db, project_id, kind)
    if draft is None:
        raise HTTPException(status_code=404, detail="尚无大纲草稿")
    if kind == "tender":
        OutlineTree.model_validate(draft.tree)
        version = p.outline_version + 1
        db.add(OutlineSnapshot(
            project_id=project_id, doc_kind="tender", version=version, tree=draft.tree, created_by=user.id
        ))
        p.outline_version = version
        p.state = "outline_confirmed"
        db.commit()
        return {"version": version, "state": p.state}

    BidOutlineTree.model_validate(draft.tree)
    version = p.bid_outline_version + 1
    db.add(OutlineSnapshot(
        project_id=project_id, doc_kind="bid", version=version, tree=draft.tree, created_by=user.id
    ))
    p.bid_outline_version = version
    chapter_count = materialize_bid_chapters(db, p, draft.tree)
    p.state = "wb_ready"
    db.commit()
    return {"version": version, "state": p.state, "chapters": chapter_count}
