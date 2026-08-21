"""逐章起草服务（Q22 定稿：章节级任务记录 + 自写轻编排，Celery 只当执行器）。

流程：确认大纲 → 展开叶章节为 chapters 行 → dispatcher 建 chapter_tasks 并逐章派发
→ run_draft_chapter 组装上下文（大纲节点+评分原文+技术需求+素材占位）→ LLM 起草 → 版本快照入库。
"""
import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.llm import LLMError, chat_structured
from app.models.chapter import Chapter, ChapterTask, ChapterVersion
from app.models.outline import OutlineSnapshot
from app.models.project import Project
from app.models.scoring_item import ScoringItemRow
from app.models.tech_requirement import TechRequirementRow
from app.schemas.draft import ChapterDraft

DRAFT_SYSTEM_PROMPT = """你是技术标书起草专家。任务：根据章节大纲与招标依据，撰写一个章节的正文 markdown。

硬约束（违反任何一条即废稿）：
1. 公司事实（业绩、人员、资质、专利等）只准引用提供的素材卡原文，不准编造；缺素材处显式标 [待补：具体缺什么]。
2. 技术响应值必须不低于招标要求（招标要求 X，我方响应 ≥X 或优于 X）。
3. 技术卷禁止出现任何报价、价格信息。
4. 评分标准原文中要求的每个要素都要在正文中有明确响应段落。
5. 不照抄招标原文做正文（要用我方方案语言改写），但 ★/☆ 条款的响应表述须逐条对应。
6. 输出 markdown：章标题用 ##，节标题用 ###，表格用 markdown 表格，图位标 [图：xxx] 占位。
7. 篇幅接近目标字数（±20%）。"""


def _leaf_nodes(nodes: list[dict]) -> list[dict]:
    """展开大纲树为叶章节序列（含父章标题路径）。"""
    out = []

    def walk(ns: list[dict], path: list[str]):
        for n in ns:
            children = n.get("children") or []
            if children:
                walk(children, path + [n["title"]])
            else:
                out.append({**n, "path": path})

    walk(nodes, [])
    return out


def materialize_chapters(db: Session, project: Project) -> int:
    """确认大纲后展开叶章节为 chapters 行（幂等：已存在则跳过）。返回新建行数。"""
    snap = (
        db.query(OutlineSnapshot)
        .filter(OutlineSnapshot.project_id == project.id, OutlineSnapshot.version == project.outline_version)
        .first()
    )
    if snap is None:
        raise ValueError(f"项目 {project.id} 无大纲快照 v{project.outline_version}")
    leaves = _leaf_nodes(snap.tree.get("nodes", []))
    created = 0
    for order, leaf in enumerate(leaves):
        exists = (
            db.query(Chapter)
            .filter(Chapter.project_id == project.id, Chapter.chapter_key == leaf["id"])
            .first()
        )
        if exists:
            continue
        db.add(Chapter(
            project_id=project.id,
            chapter_key=leaf["id"],
            title=leaf["title"],
            target_words=leaf.get("target_words", 2000),
            scoring_keys=",".join(leaf.get("scoring_keys") or []),
            sort_order=order,
            outline_version=project.outline_version,
        ))
        created += 1
    db.commit()
    return created


def dispatch_draft_all(project_id: int) -> dict:
    """dispatcher：为全部 pending 章节建任务记录并逐章派发（Q22）。"""
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project is None or project.state != "outline_confirmed":
            return {"dispatched": 0, "error": f"状态 {project and project.state} 不允许起草"}
        materialize_chapters(db, project)
        chapters = (
            db.query(Chapter)
            .filter(Chapter.project_id == project_id, Chapter.state.in_(["pending", "draft_failed"]))
            .order_by(Chapter.sort_order)
            .all()
        )
        project.state = "drafting"
        db.commit()
        n = 0
        for ch in chapters:
            task = ChapterTask(chapter_id=ch.id, project_id=project_id, state="pending")
            db.add(task)
            db.flush()
            ch.state = "drafting"
            n += 1
        db.commit()
        for ch in chapters:
            _dispatch_one(project_id, ch.id)
        return {"dispatched": n}
    finally:
        db.close()


def _dispatch_one(project_id: int, chapter_id: int) -> None:
    if settings.sync_tasks:
        run_draft_chapter(project_id, chapter_id)
    else:
        from app.worker import celery_app

        celery_app.send_task("app.worker.draft_chapter", args=[project_id, chapter_id])


def _build_context(db: Session, project: Project, chapter: Chapter) -> str:
    """上下文组装：章节信息 + 挂接评分点原文 + 技术需求原文 + 素材占位（素材库后续迭代）。"""
    parts = [f"# 章节信息\n编号：{chapter.chapter_key}\n标题：{chapter.title}\n目标字数：{chapter.target_words}"]

    keys = [k for k in chapter.scoring_keys.split(",") if k]
    if keys:
        items = (
            db.query(ScoringItemRow)
            .filter(ScoringItemRow.project_id == project.id, ScoringItemRow.item_key.in_(keys))
            .all()
        )
        if items:
            parts.append("# 本章须响应的评分标准原文")
            for it in items:
                parts.append(f"## {it.item_key} {it.item}（{it.score} 分）\n{it.criteria_original}")

    reqs = (
        db.query(TechRequirementRow)
        .filter(TechRequirementRow.project_id == project.id)
        .all()
    )
    if reqs:
        stars = [r for r in reqs if r.star]
        if stars:
            parts.append("# ★/▲ 硬指标（不得负偏离，逐条响应）")
            for r in stars:
                parts.append(f"- {r.requirement_original}")

    parts.append("# 素材库\n（素材库模块尚未上线，涉及公司业绩/人员/资质处一律标 [待补：xxx]）")
    return "\n\n".join(parts)


def run_draft_chapter(project_id: int, chapter_id: int) -> None:
    """单章起草：上下文组装 → LLM → 版本快照。任务与章节状态自管。"""
    db = SessionLocal()
    try:
        chapter = db.get(Chapter, chapter_id)
        project = db.get(Project, project_id)
        if chapter is None or project is None:
            return
        task = (
            db.query(ChapterTask)
            .filter(ChapterTask.chapter_id == chapter_id)
            .order_by(ChapterTask.id.desc())
            .first()
        )
        if task:
            task.state = "running"
            db.commit()
        try:
            context = _build_context(db, project, chapter)
            result = chat_structured(DRAFT_SYSTEM_PROMPT, context, ChapterDraft)
            _save_version(db, chapter, result.content_md, source="ai_chapter", user_id=None)
            chapter.state = "draft_done"
            chapter.draft_error = ""
            if task:
                task.state = "done"
                task.finished_at = datetime.now(timezone.utc)
            db.commit()
        except LLMError as e:
            _draft_fail(db, chapter, task, str(e))
        except Exception as e:
            _draft_fail(db, chapter, task, f"{type(e).__name__}: {e}")
        _maybe_finish_project(db, project_id)
    finally:
        db.close()


def _draft_fail(db: Session, chapter: Chapter, task: ChapterTask | None, msg: str) -> None:
    chapter.state = "draft_failed"
    chapter.draft_error = msg[:2000]
    if task:
        task.state = "failed"
        task.retry_count += 1
        task.error = msg[:2000]
        task.finished_at = datetime.now(timezone.utc)
    db.commit()


def _maybe_finish_project(db: Session, project_id: int) -> None:
    """全部章节终态后项目状态推进 draft_done（dispatcher 收尾）。"""
    project = db.get(Project, project_id)
    if project is None or project.state != "drafting":
        return
    open_states = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.state.in_(["pending", "drafting"]))
        .count()
    )
    if open_states == 0:
        project.state = "draft_done"
        db.commit()


def _save_version(db: Session, chapter: Chapter, content_md: str, source: str, user_id: int | None) -> ChapterVersion:
    """全量快照（Q24）：version_no 递增，当前正文 = 最新版本行。"""
    latest = (
        db.query(ChapterVersion)
        .filter(ChapterVersion.chapter_id == chapter.id)
        .order_by(ChapterVersion.version_no.desc())
        .first()
    )
    version_no = (latest.version_no if latest else 0) + 1
    # 中文字数按字符计（去掉 markdown 标记与空白）
    words = sum(1 for c in content_md if not c.isspace())
    v = ChapterVersion(
        chapter_id=chapter.id, version_no=version_no, content_md=content_md,
        word_count=words, source=source, created_by=user_id,
    )
    db.add(v)
    db.flush()
    return v
