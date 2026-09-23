"""标书工作台路由：检查触发/进度/发现确认与一键修复 / 投标文件导出 / 检查报告。"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.storage import storage
from app.models.project import Project
from app.models.scoring_item import ScoringItemRow
from app.models.user import User
from app.models.workbench import CHECK_TYPES, FINDING_STATUSES, CheckRun, Finding, ScoreEstimate
from app.services.export_service import (
    WORKBENCH_EXPORT_REL, build_workbench_check_report, run_workbench_export,
)
from app.services.workbench_check_service import dispatch_check, fix_all_typos, fix_finding

router = APIRouter(prefix="/api/projects/{project_id}", tags=["workbench"])

# M3：废标项/错别字；M4：逻辑谬误/技术评分（scoring 产 score_estimates 而非 findings）
OPEN_CHECK_TYPES = ["disqualification", "typo", "logic", "scoring"]


class CheckCreate(BaseModel):
    check_type: str
    chapter_keys: list[str] = []  # 空 = 全书


class RunOut(BaseModel):
    id: int
    check_type: str
    scope: str
    chapter_keys: list
    state: str
    progress: int
    progress_total: int
    stats: dict
    error: str
    created_at: str
    finished_at: str


class FindingOut(BaseModel):
    id: int
    run_id: int
    chapter_key: str
    check_type: str
    severity: str
    tender_basis: str
    bid_evidence: str
    location: str
    analysis: str
    suggestion: str
    confirm_status: str
    created_at: str


class FindingPatch(BaseModel):
    confirm_status: str


class ScoreEstimateOut(BaseModel):
    id: int
    item_key: str
    item: str
    criteria_original: str
    max_score: float
    estimated_score: float
    evidence: str
    deduction_reasons: str
    improvement_advice: str
    manual_score: float | None
    manual_note: str
    status: str
    run_id: int | None


class ScoreEstimatePatch(BaseModel):
    manual_score: float | None = None
    manual_note: str | None = None
    status: str | None = None


def _run_out(r: CheckRun) -> RunOut:
    return RunOut(
        id=r.id, check_type=r.check_type, scope=r.scope, chapter_keys=r.chapter_keys or [],
        state=r.state, progress=r.progress, progress_total=r.progress_total,
        stats=r.stats or {}, error=r.error,
        created_at=r.created_at.isoformat() if r.created_at else "",
        finished_at=r.finished_at.isoformat() if r.finished_at else "",
    )


def _finding_out(f: Finding) -> FindingOut:
    return FindingOut(
        id=f.id, run_id=f.run_id, chapter_key=f.chapter_key, check_type=f.check_type,
        severity=f.severity, tender_basis=f.tender_basis, bid_evidence=f.bid_evidence,
        location=f.location, analysis=f.analysis, suggestion=f.suggestion,
        confirm_status=f.confirm_status, created_at=f.created_at.isoformat() if f.created_at else "",
    )


def _get_wb_project(db: Session, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if p.mode != "workbench":
        raise HTTPException(status_code=400, detail="仅标书工作台项目支持检查")
    return p


@router.post("/checks", response_model=RunOut)
def create_check(
    project_id: int, body: CheckCreate,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """手动触发一次检查（页签级）。建 run 行即返回；同步模式执行完才返回。"""
    p = _get_wb_project(db, project_id)
    if p.state != "wb_ready":
        raise HTTPException(status_code=409, detail=f"当前状态 {p.state} 不允许执行检查（须先完成目录确认）")
    if body.check_type not in OPEN_CHECK_TYPES:
        raise HTTPException(status_code=400, detail=f"check_type 须为 {'/'.join(OPEN_CHECK_TYPES)}（logic/scoring 在 M4）")
    # 同类型已有 running 的 run：拒绝重复触发
    running = (
        db.query(CheckRun)
        .filter(CheckRun.project_id == project_id, CheckRun.check_type == body.check_type, CheckRun.state == "running")
        .first()
    )
    if running is not None:
        raise HTTPException(status_code=409, detail=f"已有进行中的{body.check_type}检查（run #{running.id}），请等待完成")
    run = dispatch_check(project_id, body.check_type, body.chapter_keys, user.id)
    db.expire_all()
    fresh = db.get(CheckRun, run.id)
    return _run_out(fresh or run)


@router.get("/checks/runs", response_model=list[RunOut])
def list_runs(
    project_id: int, check_type: str = "",
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    _get_wb_project(db, project_id)
    q = db.query(CheckRun).filter(CheckRun.project_id == project_id)
    if check_type:
        q = q.filter(CheckRun.check_type == check_type)
    return [_run_out(r) for r in q.order_by(CheckRun.id.desc()).limit(20).all()]


@router.get("/findings", response_model=list[FindingOut])
def list_findings(
    project_id: int, check_type: str = "", run_id: int = 0,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """默认返回该类型最新一次 run 的 findings；run_id 显式指定时查历史 run。"""
    _get_wb_project(db, project_id)
    q = db.query(Finding).filter(Finding.project_id == project_id)
    if run_id:
        q = q.filter(Finding.run_id == run_id)
    elif check_type:
        latest = (
            db.query(CheckRun)
            .filter(CheckRun.project_id == project_id, CheckRun.check_type == check_type)
            .order_by(CheckRun.id.desc())
            .first()
        )
        if latest is None:
            return []
        q = q.filter(Finding.run_id == latest.id)
    return [_finding_out(f) for f in q.order_by(Finding.severity.desc(), Finding.id).limit(500).all()]


@router.patch("/findings/{finding_id}", response_model=FindingOut)
def patch_finding(
    project_id: int, finding_id: int, body: FindingPatch,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """人工确认流：pending → confirmed（属实）/ dismissed（误报）/ fixed（已修复）。"""
    _get_wb_project(db, project_id)
    if body.confirm_status not in FINDING_STATUSES:
        raise HTTPException(status_code=400, detail=f"confirm_status 须为 {'/'.join(FINDING_STATUSES)}")
    f = db.get(Finding, finding_id)
    if f is None or f.project_id != project_id:
        raise HTTPException(status_code=404, detail="检查发现不存在")
    f.confirm_status = body.confirm_status
    f.confirmed_by = user.id
    f.confirmed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(f)
    return _finding_out(f)


@router.post("/findings/{finding_id}/fix", response_model=FindingOut)
def fix_one_finding(
    project_id: int, finding_id: int,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """错别字一键修复：定位替换落 fix 版本，finding 置已修复。正文已变则 409 提示重查。"""
    _get_wb_project(db, project_id)
    f = db.get(Finding, finding_id)
    if f is None or f.project_id != project_id:
        raise HTTPException(status_code=404, detail="检查发现不存在")
    try:
        fix_finding(db, f, user.id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    db.refresh(f)
    return _finding_out(f)


@router.post("/findings/fix-all")
def fix_all_findings(
    project_id: int,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """批量修复最新错别字 run 的全部未处理发现（定位失效的跳过并回报）。"""
    _get_wb_project(db, project_id)
    return fix_all_typos(db, project_id, user.id)


@router.post("/wb/export")
def export_workbench(
    project_id: int,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """导出润色/修复后的投标文件 docx（按投标目录树层级渲染，可反复导出）。"""
    p = _get_wb_project(db, project_id)
    result = run_workbench_export(project_id)
    if "error" in result:
        raise HTTPException(status_code=409, detail=result["error"])
    return result


@router.get("/wb/export/docx")
def download_workbench_docx(
    project_id: int,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """下载工作台导出的 docx（不存在则现场生成）。"""
    _get_wb_project(db, project_id)
    rel = WORKBENCH_EXPORT_REL.format(pid=project_id)
    if not storage.exists(rel):
        result = run_workbench_export(project_id)
        if "error" in result:
            raise HTTPException(status_code=409, detail=result["error"])
    return FileResponse(
        storage.abspath(rel),
        filename="投标文件-工作台版.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@router.get("/wb/check-report")
def download_workbench_report(
    project_id: int,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """下载检查报告 markdown（现场生成，含四类检查最新结果与模拟评分）。"""
    _get_wb_project(db, project_id)
    rel, _ = build_workbench_check_report(project_id)
    return FileResponse(storage.abspath(rel), filename="标书检查报告.md",
                        media_type="text/markdown; charset=utf-8")


@router.get("/score-estimates", response_model=list[ScoreEstimateOut])
def list_score_estimates(
    project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """技术评分点 × 模拟估分（AI 先估、用户可手工修正；仅技术类评分点参与）。"""
    _get_wb_project(db, project_id)
    rows = (
        db.query(ScoreEstimate, ScoringItemRow)
        .join(ScoringItemRow, ScoreEstimate.scoring_item_id == ScoringItemRow.id)
        .filter(ScoreEstimate.project_id == project_id)
        .order_by(ScoringItemRow.item_key)
        .all()
    )
    return [
        ScoreEstimateOut(
            id=e.id, item_key=i.item_key, item=i.item, criteria_original=i.criteria_original,
            max_score=e.max_score, estimated_score=e.estimated_score,
            evidence=e.evidence, deduction_reasons=e.deduction_reasons,
            improvement_advice=e.improvement_advice,
            manual_score=e.manual_score, manual_note=e.manual_note,
            status=e.status, run_id=e.run_id,
        )
        for e, i in rows
    ]


@router.patch("/score-estimates/{estimate_id}", response_model=ScoreEstimateOut)
def patch_score_estimate(
    project_id: int, estimate_id: int, body: ScoreEstimatePatch,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """手工修正估分：填 manual_score 即为修正（status→adjusted）；确认无误可 confirm。
    重新估分只覆盖 AI 字段，手工修正保留。"""
    _get_wb_project(db, project_id)
    e = db.get(ScoreEstimate, estimate_id)
    if e is None or e.project_id != project_id:
        raise HTTPException(status_code=404, detail="估分记录不存在")
    if body.status is not None and body.status not in ("ai", "adjusted", "confirmed"):
        raise HTTPException(status_code=400, detail="status 须为 ai/adjusted/confirmed")
    if body.manual_score is not None:
        if not 0 <= body.manual_score <= e.max_score:
            raise HTTPException(status_code=400, detail=f"修正分须在 0-{e.max_score} 之间")
        e.manual_score = body.manual_score
        if body.status is None:
            e.status = "adjusted"
    if body.manual_note is not None:
        e.manual_note = body.manual_note[:2000]
    if body.status is not None:
        e.status = body.status
    db.commit()
    db.refresh(e)
    item = db.get(ScoringItemRow, e.scoring_item_id)
    return ScoreEstimateOut(
        id=e.id, item_key=item.item_key, item=item.item, criteria_original=item.criteria_original,
        max_score=e.max_score, estimated_score=e.estimated_score,
        evidence=e.evidence, deduction_reasons=e.deduction_reasons,
        improvement_advice=e.improvement_advice,
        manual_score=e.manual_score, manual_note=e.manual_note,
        status=e.status, run_id=e.run_id,
    )
