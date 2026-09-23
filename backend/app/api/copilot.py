"""Copilot 打点与统计路由：应用打点（北极星数据源）+ 按动作/模型聚合的应用率报表。"""
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.copilot import CopilotAction
from app.models.user import User

router = APIRouter(prefix="/api", tags=["copilot"])


class CopilotStatRow(BaseModel):
    key: str
    total: int
    applied: int
    apply_rate: float
    avg_latency_ms: int
    avg_output_chars: int


class InstructionStat(BaseModel):
    instruction: str
    count: int


class CopilotStatsOut(BaseModel):
    total: int
    applied: int
    apply_rate: float
    by_action: list[CopilotStatRow]
    by_model: list[CopilotStatRow]
    top_instructions: list[InstructionStat]  # 高频自定义指令 → 候选固化为一键动作


@router.patch("/projects/{project_id}/copilot-actions/{action_id}/applied")
def mark_applied(
    project_id: int, action_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    row = db.get(CopilotAction, action_id)
    if row is None or row.project_id != project_id:
        raise HTTPException(status_code=404, detail="动作记录不存在")
    if not row.applied:
        row.applied = True
        row.applied_at = datetime.now(timezone.utc)
        db.commit()
    return {"applied": True}


def _aggregate(rows: list[CopilotAction], key_fn) -> list[CopilotStatRow]:
    groups: dict[str, list[CopilotAction]] = defaultdict(list)
    for r in rows:
        groups[key_fn(r) or "(未知)"].append(r)
    out = []
    for key, rs in groups.items():
        applied = sum(1 for r in rs if r.applied)
        out.append(CopilotStatRow(
            key=key, total=len(rs), applied=applied,
            apply_rate=round(applied / len(rs), 3),
            avg_latency_ms=int(sum(r.latency_ms for r in rs) / len(rs)),
            avg_output_chars=int(sum(len(r.output_md) for r in rs) / len(rs)),
        ))
    return sorted(out, key=lambda x: -x.total)


@router.get("/copilot-stats", response_model=CopilotStatsOut)
def copilot_stats(
    project_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """应用率仪表盘。数据量为动作日志级（每次点击一行），内存聚合足够。"""
    q = db.query(CopilotAction)
    if project_id is not None:
        q = q.filter(CopilotAction.project_id == project_id)
    rows = q.all()
    applied = sum(1 for r in rows if r.applied)
    instr: dict[str, int] = defaultdict(int)
    for r in rows:
        if r.instruction:
            instr[r.instruction] += 1
    top = sorted(instr.items(), key=lambda x: -x[1])[:10]
    return CopilotStatsOut(
        total=len(rows), applied=applied,
        apply_rate=round(applied / len(rows), 3) if rows else 0.0,
        by_action=_aggregate(rows, lambda r: r.action),
        by_model=_aggregate(rows, lambda r: r.model),
        top_instructions=[InstructionStat(instruction=i, count=c) for i, c in top],
    )
