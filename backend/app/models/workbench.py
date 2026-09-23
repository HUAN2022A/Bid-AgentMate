"""标书工作台：检查运行 + 检查发现 + 技术模拟评分。

- check_runs：一次手动触发的检查/评分任务（按页签），行级状态机 running → done | failed，
  长任务不占 project.state；重跑 = 新 run，findings 按run隔离（UI 展示最新 run）。
- findings：一条可追溯的检查发现（废标项/错别字/逻辑谬误），八字段齐全 + 人工确认状态。
  检查发现可能只是待人工确认的风险，不等于问题成立。
- score_estimates：每个技术评分点一行的模拟估分（带证据/扣分理由/建议），
  AI 先估、用户可手工修正；模拟估分不代表正式评标结果。
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.outline import JSONBCompat
CHECK_TYPES = ["disqualification", "typo", "logic", "scoring"]

RUN_STATES = ["running", "done", "failed"]

SEVERITIES = ["high", "medium", "low"]

# 人工确认状态：pending 待确认 | confirmed 属实确认 | dismissed 误报忽略 | fixed 已修复
FINDING_STATUSES = ["pending", "confirmed", "dismissed", "fixed"]

# 估分状态：ai 纯 AI 估分 | adjusted 已手工修正 | confirmed 已人工确认
ESTIMATE_STATUSES = ["ai", "adjusted", "confirmed"]


class CheckRun(Base):
    __tablename__ = "check_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    check_type: Mapped[str] = mapped_column(String(16), index=True)  # CHECK_TYPES
    scope: Mapped[str] = mapped_column(String(16), default="all")  # all | chapters
    chapter_keys: Mapped[list] = mapped_column(JSONBCompat, default=list)  # scope=chapters 时的章节范围
    state: Mapped[str] = mapped_column(String(16), default="running", index=True)
    progress: Mapped[int] = mapped_column(default=0)  # 已处理块数/总块数（前端进度条）
    progress_total: Mapped[int] = mapped_column(default=0)
    stats: Mapped[str] = mapped_column(JSONBCompat, default=dict)  # {high/medium/low 计数, run 摘要}
    error: Mapped[str] = mapped_column(String(2000), default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("check_runs.id"), index=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id"), nullable=True, index=True)
    chapter_key: Mapped[str] = mapped_column(String(32), default="")  # 冗余章节号，项目级发现（如废标项）可空
    check_type: Mapped[str] = mapped_column(String(16), index=True)
    severity: Mapped[str] = mapped_column(String(8), default="medium", index=True)
    tender_basis: Mapped[str] = mapped_column(Text, default="")  # 招标依据（条款原文引用）
    bid_evidence: Mapped[str] = mapped_column(Text, default="")  # 投标文件证据（原文精确片段）
    location: Mapped[str] = mapped_column(String(512), default="")  # 原文位置说明
    analysis: Mapped[str] = mapped_column(Text, default="")  # 分析说明
    suggestion: Mapped[str] = mapped_column(Text, default="")  # 修改建议
    fix: Mapped[dict] = mapped_column(JSONBCompat, default=dict)  # 错别字修复数据 {span, corrected}（一键修复用）
    confirm_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    confirmed_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScoreEstimate(Base):
    __tablename__ = "score_estimates"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    scoring_item_id: Mapped[int] = mapped_column(ForeignKey("scoring_items.id"), index=True)
    max_score: Mapped[float] = mapped_column(Float)
    estimated_score: Mapped[float] = mapped_column(Float)  # AI 预估得分（用户修正前）
    evidence: Mapped[str] = mapped_column(Text, default="")  # 投标证据引用（JSON：[{chapter_key, quote}]）
    deduction_reasons: Mapped[str] = mapped_column(Text, default="")  # 扣分理由
    improvement_advice: Mapped[str] = mapped_column(Text, default="")  # 改进建议
    manual_score: Mapped[float] = mapped_column(Float, nullable=True)  # 用户手工修正分（空 = 未修正）
    manual_note: Mapped[str] = mapped_column(String(2000), default="")
    status: Mapped[str] = mapped_column(String(16), default="ai", index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("check_runs.id"), nullable=True)  # 最近一次估分 run
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
