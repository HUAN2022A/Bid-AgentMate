"""作战大屏路由：单接口聚合项目全量作战指标（只读实时计算，无副作用）。"""
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.chapter import Chapter, ChapterVersion
from app.models.copilot import CopilotAction
from app.models.material import MATERIAL_TYPES, Material
from app.models.project import PROJECT_STATES, Project
from app.models.scoring_item import ScoringItemRow
from app.models.tech_requirement import TechRequirementRow
from app.models.user import User
from app.services.check_service import (
    build_coverage,
    extract_hard_keywords,
    find_keyword_hits,
    latest_contents,
)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["screen"])

# 与 check_service.run_check 同款检测模式（该处内联在报告生成里，此处按原样复用）
PENDING_RE = re.compile(r"\[待补[^]]*\]")
PRICE_RE = re.compile(r"(报价|投标价|总价款|人民币\s*\d)")


class ScreenProjectOut(BaseModel):
    id: int
    name: str
    tender_no: str
    state: str
    updated_at: str


class ScreenSummaryOut(BaseModel):
    total_words: int
    target_words: int
    done_chapters: int
    total_chapters: int
    pending_gaps: int
    price_hits: int
    star_reqs: int
    star_hit: int


class ScoreDistRow(BaseModel):
    category: str
    score: float
    count: int


class CoverageChapterOut(BaseModel):
    key: str
    title: str


class CoverageItemOut(BaseModel):
    key: str
    item: str
    score: float
    cells: str  # 逗号分隔，split(",") 后与 chapters 下标对齐，取值 covered/partial/none


class CoverageSummaryOut(BaseModel):
    covered: int
    partial: int
    none: int


class CoverageOut(BaseModel):
    chapters: list[CoverageChapterOut]
    items: list[CoverageItemOut]
    summary: CoverageSummaryOut


class ChapterRowOut(BaseModel):
    key: str
    title: str
    words: int
    target: int
    state: str


class CopilotRecentOut(BaseModel):
    action: str
    applied: bool
    at: str
    model: str
    instruction: str


class CopilotOut(BaseModel):
    total: int
    applied: int
    apply_rate: float
    recent: list[CopilotRecentOut]


class MaterialsOut(BaseModel):
    case: int
    person: int
    credential: int
    ip: int
    capability: int


class ScreenDataOut(BaseModel):
    project: ScreenProjectOut
    flow: list[str]
    current_step_index: int
    summary: ScreenSummaryOut
    score_dist: list[ScoreDistRow]
    coverage: CoverageOut
    chapters: list[ChapterRowOut]
    copilot: CopilotOut
    materials: MaterialsOut
    risks: list[str]


def _get_project(db: Session, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return p


@router.get("/screen-data", response_model=ScreenDataOut)
def screen_data(
    project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """作战大屏数据聚合：进度/覆盖/评分分布/copilot 应用率/素材库/风险，一次拉全。

    全部实时计算、幂等无副作用；覆盖矩阵、硬指标关键词、最新正文均复用
    check_service 的公开函数，与自查报告同一套判定语义。
    """
    p = _get_project(db, project_id)

    # ---- 章节进度（正文 = 最新版本行，口径同 export/check） ----
    contents = latest_contents(db, project_id)
    chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id)
        .order_by(Chapter.sort_order, Chapter.chapter_key)
        .all()
    )
    chapters_out: list[dict] = []
    total_words = 0
    target_words = 0
    pending_by_chapter: list[tuple[str, int]] = []  # (chapter_key, 待补数)，按章节序
    pending_gaps = 0
    price_hits = 0
    for ch in chapters:
        v = (
            db.query(ChapterVersion)
            .filter(ChapterVersion.chapter_id == ch.id)
            .order_by(ChapterVersion.version_no.desc())
            .first()
        )
        words = v.word_count if v else 0
        txt = contents.get(ch.chapter_key, "")
        n_pending = len(PENDING_RE.findall(txt))
        n_price = len(PRICE_RE.findall(txt))
        pending_gaps += n_pending
        price_hits += n_price
        if txt:  # 有正文的章节才计字数（同 export_service）
            total_words += words
        target_words += ch.target_words
        if n_pending:
            pending_by_chapter.append((ch.chapter_key, n_pending))
        chapters_out.append(
            {"key": ch.chapter_key, "title": ch.title, "words": words,
             "target": ch.target_words, "state": ch.state}
        )

    # ---- 星级硬指标命中（复用 check_service 的关键词函数，判定语义同 run_check ★节） ----
    star_rows = (
        db.query(TechRequirementRow)
        .filter(TechRequirementRow.project_id == project_id, TechRequirementRow.star == True)  # noqa: E712
        .order_by(TechRequirementRow.req_key)
        .all()
    )
    star_hit = 0
    missed_star: list[str] = []
    for r in star_rows:
        orig = r.requirement_original.strip().replace("\n", " ")
        kws = extract_hard_keywords(orig)
        if not kws:
            kws = [orig[:8]]  # 无数字指标兜底，同 run_check
        if any(find_keyword_hits(kws, t) for t in contents.values()):
            star_hit += 1
        else:
            missed_star.append(r.req_key)

    # ---- 评分点分布（按 category 聚合：分值和 + 条数） ----
    dist: dict[str, dict] = {}
    for it in (
        db.query(ScoringItemRow).filter(ScoringItemRow.project_id == project_id).all()
    ):
        d = dist.setdefault(it.category, {"category": it.category, "score": 0.0, "count": 0})
        d["score"] += it.score
        d["count"] += 1
    score_dist = sorted(dist.values(), key=lambda d: (-d["score"], d["category"]))
    for d in score_dist:
        d["score"] = round(d["score"], 2)

    # ---- 覆盖矩阵（build_coverage 压平：cells 逗号串与 chapters 下标对齐） ----
    cov = build_coverage(db, project_id)
    coverage_out = {
        "chapters": [
            {"key": c["chapter_key"], "title": c["title"]} for c in cov["chapters"]
        ],
        "items": [
            {
                "key": i["item_key"],
                "item": i["item"],
                "score": i["score"],
                "cells": ",".join(c["status"] for c in i["cells"]),
            }
            for i in cov["items"]
        ],
        "summary": {
            "covered": cov["summary"]["covered"],
            "partial": cov["summary"]["partial"],
            "none": cov["summary"]["none"],
        },
    }

    # ---- Copilot 应用率（统计口径同 copilot_stats；recent 最近 10 条倒序） ----
    cop_rows = db.query(CopilotAction).filter(CopilotAction.project_id == project_id).all()
    cop_applied = sum(1 for r in cop_rows if r.applied)
    recent_rows = (
        db.query(CopilotAction)
        .filter(CopilotAction.project_id == project_id)
        .order_by(CopilotAction.created_at.desc(), CopilotAction.id.desc())
        .limit(10)
        .all()
    )
    copilot_out = {
        "total": len(cop_rows),
        "applied": cop_applied,
        "apply_rate": round(cop_applied / len(cop_rows), 3) if cop_rows else 0.0,
        "recent": [
            {
                "action": r.action,
                "applied": r.applied,
                "at": r.created_at.isoformat() if r.created_at else "",
                "model": r.model,
                "instruction": r.instruction,
            }
            for r in recent_rows
        ],
    }

    # ---- 素材库存量（全局素材库按 type 计数，五类固定键） ----
    mat_counts = dict(
        db.query(Material.type, func.count(Material.id)).group_by(Material.type).all()
    )
    materials_out = {t: mat_counts.get(t, 0) for t in MATERIAL_TYPES}

    # ---- 风险清单（最多 6 条中文描述，无则空数组） ----
    risks: list[str] = []
    for key, n in pending_by_chapter:
        risks.append(f"章节 {key} 有 {n} 处 [待补]")
    for rk in missed_star:
        risks.append(f"星级条款 {rk} 未命中")
    if price_hits:
        risks.append(f"检测到价格表述 {price_hits} 处")
    risks = risks[:6]

    return ScreenDataOut(
        project=ScreenProjectOut(
            id=p.id,
            name=p.name,
            tender_no=p.tender_no,
            state=p.state,
            updated_at=p.updated_at.isoformat() if p.updated_at else "",
        ),
        flow=list(PROJECT_STATES),
        current_step_index=PROJECT_STATES.index(p.state) if p.state in PROJECT_STATES else 0,
        summary=ScreenSummaryOut(
            total_words=total_words,
            target_words=target_words,
            done_chapters=len(contents),
            total_chapters=len(chapters),
            pending_gaps=pending_gaps,
            price_hits=price_hits,
            star_reqs=len(star_rows),
            star_hit=star_hit,
        ),
        score_dist=[ScoreDistRow(**d) for d in score_dist],
        coverage=CoverageOut(
            chapters=[CoverageChapterOut(**c) for c in coverage_out["chapters"]],
            items=[CoverageItemOut(**i) for i in coverage_out["items"]],
            summary=CoverageSummaryOut(**coverage_out["summary"]),
        ),
        chapters=[ChapterRowOut(**c) for c in chapters_out],
        copilot=CopilotOut(**copilot_out),
        materials=MaterialsOut(**materials_out),
        risks=risks,
    )
