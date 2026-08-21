"""章节路由：起草触发/章节列表/正文读写（版本快照 Q24）/版本历史。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.chapter import Chapter, ChapterVersion
from app.models.project import Project
from app.models.user import User
from app.services.draft_service import _save_version, dispatch_draft_all

router = APIRouter(prefix="/api/projects/{project_id}/chapters", tags=["chapters"])


class ChapterOut(BaseModel):
    id: int
    chapter_key: str
    title: str
    target_words: int
    scoring_keys: str
    state: str
    draft_error: str
    needs_review: bool
    word_count: int  # 最新版本字数，0 = 未起草


class ChapterContentOut(BaseModel):
    id: int
    chapter_key: str
    title: str
    state: str
    content_md: str
    version_no: int
    word_count: int
    target_words: int


class ChapterSaveIn(BaseModel):
    content_md: str


class VersionOut(BaseModel):
    version_no: int
    source: str
    word_count: int
    created_at: str


def _get_project(db: Session, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return p


def _latest_version(db: Session, chapter_id: int) -> ChapterVersion | None:
    return (
        db.query(ChapterVersion)
        .filter(ChapterVersion.chapter_id == chapter_id)
        .order_by(ChapterVersion.version_no.desc())
        .first()
    )


@router.post("/draft-all")
def draft_all(
    project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """dispatcher 入口：全部 pending/draft_failed 章节逐章起草（Q22）。

    允许 outline_confirmed（首起草）/ draft_done（重跑失败）/ exported（补起草，如素材库更新后）。
    """
    p = _get_project(db, project_id)
    if p.state not in ("outline_confirmed", "draft_done", "exported"):
        raise HTTPException(status_code=409, detail=f"当前状态 {p.state} 不允许起草（须先确认大纲）")
    if p.state in ("draft_done", "exported"):  # 回到 outline_confirmed 让 dispatcher 接管
        p.state = "outline_confirmed"
        db.commit()
    return dispatch_draft_all(project_id)


@router.get("", response_model=list[ChapterOut])
def list_chapters(
    project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    _get_project(db, project_id)
    chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id)
        .order_by(Chapter.sort_order)
        .all()
    )
    out = []
    for ch in chapters:
        v = _latest_version(db, ch.id)
        out.append(ChapterOut(
            id=ch.id, chapter_key=ch.chapter_key, title=ch.title,
            target_words=ch.target_words, scoring_keys=ch.scoring_keys,
            state=ch.state, draft_error=ch.draft_error, needs_review=ch.needs_review,
            word_count=v.word_count if v else 0,
        ))
    return out


@router.get("/{chapter_id}", response_model=ChapterContentOut)
def get_chapter(
    project_id: int, chapter_id: int, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_project(db, project_id)
    ch = db.get(Chapter, chapter_id)
    if ch is None or ch.project_id != project_id:
        raise HTTPException(status_code=404, detail="章节不存在")
    v = _latest_version(db, ch.id)
    return ChapterContentOut(
        id=ch.id, chapter_key=ch.chapter_key, title=ch.title, state=ch.state,
        content_md=v.content_md if v else "", version_no=v.version_no if v else 0,
        word_count=v.word_count if v else 0, target_words=ch.target_words,
    )


@router.put("/{chapter_id}", response_model=ChapterContentOut)
def save_chapter(
    project_id: int, chapter_id: int, body: ChapterSaveIn,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """人工保存：新版本快照（source=human），章节状态转 edited。"""
    _get_project(db, project_id)
    ch = db.get(Chapter, chapter_id)
    if ch is None or ch.project_id != project_id:
        raise HTTPException(status_code=404, detail="章节不存在")
    v = _save_version(db, ch, body.content_md, source="human", user_id=user.id)
    ch.state = "edited"
    db.commit()
    return ChapterContentOut(
        id=ch.id, chapter_key=ch.chapter_key, title=ch.title, state=ch.state,
        content_md=v.content_md, version_no=v.version_no,
        word_count=v.word_count, target_words=ch.target_words,
    )


@router.get("/{chapter_id}/versions", response_model=list[VersionOut])
def list_versions(
    project_id: int, chapter_id: int, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_project(db, project_id)
    ch = db.get(Chapter, chapter_id)
    if ch is None or ch.project_id != project_id:
        raise HTTPException(status_code=404, detail="章节不存在")
    versions = (
        db.query(ChapterVersion)
        .filter(ChapterVersion.chapter_id == chapter_id)
        .order_by(ChapterVersion.version_no.desc())
        .all()
    )
    return [
        VersionOut(
            version_no=v.version_no, source=v.source, word_count=v.word_count,
            created_at=v.created_at.isoformat() if v.created_at else "",
        )
        for v in versions
    ]
