"""标书工作台检查服务：废标项裁决 + 错别字全书扫描 → check_runs（行级状态）+ findings。

- 触发即建 run 行（running），逐块推进 progress；完成落 stats，失败落 error。
- findings 属于某次 run；重跑生成新 run，历史 run 及其 findings 保留（UI 默认展示最新 run）。
- 人工确认流走 PATCH /findings/{id}（确认状态与操作人单点更新），本服务只产出 pending。
- 错别字证据回验：LLM 返回的原文片段必须在分块内逐字命中才落库（可在原文定位的验收口径）。
"""
import json
from datetime import datetime, timezone

import yaml
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.llm import chat_structured
from app.core.storage import storage
from app.models.chapter import Chapter, ChapterVersion
from app.models.project import Project
from app.models.scoring_item import ScoringItemRow
from app.models.workbench import CheckRun, Finding, ScoreEstimate
from app.schemas.workbench_check import (
    DisqScanResult, LogicScanResult, ScoreEstimateResult, TypoScanResult,
)

# 错别字分块（字符）：按行聚块，超硬上限强制断块
TYPO_CHUNK_TARGET = 3000
TYPO_CHUNK_HARD = 4000

# 废标项单次 LLM 输入预算（投标全文截断）
DISQ_TEXT_BUDGET = 60_000

TYPO_SYSTEM_PROMPT = """你是标书文字校对专家。任务：在投标文件文本片段中找出错别字。

规则：
1. 只报确定性高的错别字：同音/形近误写、明显笔误（如"按装"→"安装"、" Wich"→"Which"）。
2. 不报：行业术语、产品型号、合理简称、人名地名音译变体、与原意一致的可接受写法。
3. original 必须逐字复制原文中包含错字的连续片段（4-20 字，带少量上下文）；corrected 只改错字本身。
4. 没有把握就不报；没有错别字时输出空数组。"""

DISQ_SYSTEM_PROMPT = """你是投标文件合规审查专家。任务：逐条判断投标文件文本是否满足招标文件的废标/否决条款。

规则：
1. verdict 三态：pass=文本可证满足该条款；risk=文本存在违反该条款的风险（如出现不该出现的内容、承诺不满足）；needs_review=无法仅凭文本判断（如盖章、签名、份数、密封、原件要求）。
2. evidence 逐字引用投标文件中支持结论的原文片段；pass/risk 尽量给证据，needs_review 可留空。
3. analysis 说明判断依据；suggestion 给出修改建议或需人工复核的具体动作（如"现场核查投标函盖章"）。
4. 每条条款都必须给出裁决，不得遗漏；clause_id 原样返回。"""

LOGIC_SYSTEM_PROMPT = """你是投标文件逻辑审查专家。任务：在单章正文中找出确定性较高的逻辑问题。

问题类型（不限于）：前后矛盾、章节间数据不一致、以偏概全、因果倒置、承诺超出常规能力、时限/数量自相矛盾。
规则：
1. 只报有把握的问题；quote 必须逐字复制原文中体现问题的连续片段（10-60 字）。
2. analysis 说明与什么矛盾/为什么不成立；suggestion 给出可执行的修改建议。
3. 表格数据、型号参数的正常差异不算逻辑问题；没有问题就输出空数组。"""

SCORING_SYSTEM_PROMPT = """你是技术标书评标模拟专家。任务：站在评委角度，依据评分标准对投标文件逐评分点估分。

规则：
1. estimated_score 严格按评分标准的每个评分要素在投标文件中的响应充分程度估分（0 到最高分），宁可保守不高估。
2. evidence 逐字引用投标文件中支撑得分的原文片段（多处用换行分隔）；扣分要素在 deduction_reasons 逐条说明（缺什么、哪里响应不充分）。
3. improvement_advice 直接可执行：补什么内容、放在哪个章节、怎么表述能对上评分要素。
4. 这是模拟估分供投标方改进，不代表正式评标结果；每个评分点都必须给出估分，item_key 原样返回。"""


def _scope_chapters(db: Session, project_id: int, chapter_keys: list[str]) -> list[tuple[Chapter, str]]:
    """范围内有正文的章节 [(Chapter, content)]，按 sort_order。"""
    q = db.query(Chapter).filter(Chapter.project_id == project_id).order_by(Chapter.sort_order)
    chapters = q.all()
    if chapter_keys:
        wanted = set(chapter_keys)
        chapters = [c for c in chapters if c.chapter_key in wanted]
    out: list[tuple[Chapter, str]] = []
    for ch in chapters:
        v = (
            db.query(ChapterVersion)
            .filter(ChapterVersion.chapter_id == ch.id)
            .order_by(ChapterVersion.version_no.desc())
            .first()
        )
        if v and v.content_md.strip():
            out.append((ch, v.content_md))
    return out


def _chunk_lines(text: str) -> list[str]:
    """按行聚块到目标字符数（硬上限内强制断行）。"""
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for line in text.split("\n"):
        if size and size + len(line) + 1 > TYPO_CHUNK_TARGET:
            chunks.append("\n".join(buf))
            buf, size = [], 0
            # 硬上限内的超长单行直接独立成块
        buf.append(line)
        size += len(line) + 1
        if size >= TYPO_CHUNK_HARD:
            chunks.append("\n".join(buf))
            buf, size = [], 0
    if buf:
        chunks.append("\n".join(buf))
    return [c for c in chunks if c.strip()]


def _locate_chapter(chapters: list[tuple[Chapter, str]], quote: str) -> Chapter | None:
    """证据引用 → 所在章节（首命中；找不到返回 None）。"""
    q = quote.strip()
    if not q:
        return None
    for ch, content in chapters:
        if q in content:
            return ch
    return None


def _fail_run(db: Session, run: CheckRun, msg: str) -> None:
    run.state = "failed"
    run.error = msg[:2000]
    run.finished_at = datetime.now(timezone.utc)
    db.commit()


# ---------------------------------------------------------------------------
# 废标项
# ---------------------------------------------------------------------------

def _load_disq_clauses(project_id: int) -> list[dict]:
    yaml_rel = f"projects/{project_id}/tender/tender-analysis.yaml"
    if not storage.exists(yaml_rel):
        return []
    data = yaml.safe_load(storage.get(yaml_rel).decode("utf-8")) or {}
    return [d for d in data.get("disqualification", []) if not d.get("manual")]


def run_disq_check(db: Session, run: CheckRun, chapters: list[tuple[Chapter, str]]) -> None:
    clauses = _load_disq_clauses(run.project_id)
    if not clauses:
        run.stats = {"clauses": 0, "note": "招标解析结果中无废标条款（或解析快照缺失）"}
        return

    # 投标全文（按预算截断，保留各章节开头——废标证据多在函件/承诺/技术卷卷首）
    text_parts: list[str] = []
    size = 0
    for ch, content in chapters:
        piece = f"【{ch.chapter_key} {ch.title}】\n{content}"
        if size + len(piece) > DISQ_TEXT_BUDGET:
            remain = max(DISQ_TEXT_BUDGET - size, 0)
            if remain > 500:
                text_parts.append(piece[:remain] + "\n……[截断]")
            break
        text_parts.append(piece)
        size += len(piece)

    clause_block = "\n".join(
        f"[{d.get('id')}] {(d.get('clause_original') or '').strip()}" for d in clauses
    )
    user_prompt = (
        f"# 废标/否决条款（逐条裁决）\n{clause_block}\n\n"
        f"# 投标文件全文\n" + "\n\n".join(text_parts)
    )

    if settings.sample_mode:
        from app.samples.check_fixtures import sample_disq_scan
        result = sample_disq_scan(clauses)
    else:
        result = chat_structured(
            DISQ_SYSTEM_PROMPT, user_prompt, DisqScanResult, layer="copilot", timeout=600
        )

    stats = {"clauses": len(clauses), "pass": 0, "risk": 0, "needs_review": 0}
    for v in result.verdicts:
        stats[v.verdict] = stats.get(v.verdict, 0) + 1
        if v.verdict == "pass":
            continue  # 满足的条款不产 finding，只进统计
        ch = _locate_chapter(chapters, v.evidence)
        db.add(Finding(
            project_id=run.project_id, run_id=run.id, chapter_id=ch.id if ch else None,
            chapter_key=ch.chapter_key if ch else "",
            check_type="disqualification",
            severity="high" if v.verdict == "risk" else "medium",
            tender_basis=next(
                ((d.get("clause_original") or "") for d in clauses if d.get("id") == v.clause_id), ""
            ),
            bid_evidence=v.evidence, location=ch.chapter_key if ch else "",
            analysis=f"[{v.clause_id} · {v.verdict}] {v.analysis}", suggestion=v.suggestion,
        ))
    run.stats = stats


# ---------------------------------------------------------------------------
# 错别字（全书扫）
# ---------------------------------------------------------------------------

def run_typo_check(db: Session, run: CheckRun, chapters: list[tuple[Chapter, str]]) -> None:
    # (chapter, chunk) 平铺 → 全书逐块
    jobs: list[tuple[Chapter, str]] = []
    for ch, content in chapters:
        for chunk in _chunk_lines(content):
            jobs.append((ch, chunk))
    run.progress_total = len(jobs)
    db.commit()

    seen: set[tuple[int, str]] = set()  # (chapter_id, 原文片段) 去重（分块边界/重跑复扫）
    dropped = 0
    for ch, chunk in jobs:
        if settings.sample_mode:
            from app.samples.check_fixtures import sample_typo_scan
            result = sample_typo_scan(chunk)
        else:
            result = chat_structured(
                TYPO_SYSTEM_PROMPT,
                f"投标文件《{ch.title}》文本片段如下：\n\n{chunk}",
                TypoScanResult, layer="copilot", timeout=600,
            )
        for hit in result.typos:
            span = hit.original.strip()
            if not span or span not in chunk:  # 证据回验：必须逐字命中本块
                dropped += 1
                continue
            key = (ch.id, span)
            if key in seen:
                continue
            seen.add(key)
            # 证据带上下文展示（±20 字），original 片段在其中高亮定位由前端处理
            idx = chunk.index(span)
            ctx = chunk[max(0, idx - 20): idx + len(span) + 20].replace("\n", " ")
            db.add(Finding(
                project_id=run.project_id, run_id=run.id, chapter_id=ch.id, chapter_key=ch.chapter_key,
                check_type="typo", severity="low",
                bid_evidence=f"「{ctx}」", location=ch.chapter_key,
                analysis=hit.reason, suggestion=f"{span} → {hit.corrected}",
            ))
        run.progress += 1
        db.commit()
    if dropped:
        note = f"LLM 返回的 {dropped} 个片段未在原文逐字命中，已丢弃"
        run.stats = {**(run.stats or {}), "dropped": dropped, "dropped_note": note}


# ---------------------------------------------------------------------------
# 逻辑谬误（逐章）
# ---------------------------------------------------------------------------

def run_logic_check(db: Session, run: CheckRun, chapters: list[tuple[Chapter, str]]) -> None:
    run.progress_total = len(chapters)
    db.commit()
    dropped = 0
    for ch, content in chapters:
        if settings.sample_mode:
            from app.samples.check_fixtures import sample_logic_scan
            result = sample_logic_scan(content)
        else:
            result = chat_structured(
                LOGIC_SYSTEM_PROMPT,
                f"投标文件章节《{ch.chapter_key} {ch.title}》正文如下：\n\n{content}",
                LogicScanResult, layer="copilot", timeout=600,
            )
        for issue in result.issues:
            quote = issue.quote.strip()
            if not quote or quote not in content:  # 证据回验：逐字命中才落库
                dropped += 1
                continue
            hard = any(k in issue.issue_type for k in ("矛盾", "不一致"))
            db.add(Finding(
                project_id=run.project_id, run_id=run.id, chapter_id=ch.id, chapter_key=ch.chapter_key,
                check_type="logic", severity="high" if hard else "medium",
                bid_evidence=f"「{quote}」", location=ch.chapter_key,
                analysis=f"[{issue.issue_type}] {issue.analysis}", suggestion=issue.suggestion,
            ))
        run.progress += 1
        db.commit()
    if dropped:
        run.stats = {**(run.stats or {}), "dropped": dropped,
                     "dropped_note": f"{dropped} 个问题片段未在原文逐字命中，已丢弃"}


# ---------------------------------------------------------------------------
# 技术模拟评分（逐评分点一次估完；upsert 保留手工修正）
# ---------------------------------------------------------------------------

def run_scoring_check(db: Session, run: CheckRun, chapters: list[tuple[Chapter, str]]) -> None:
    items = (
        db.query(ScoringItemRow)
        .filter(ScoringItemRow.project_id == run.project_id, ScoringItemRow.category == "技术")
        .order_by(ScoringItemRow.item_key)
        .all()
    )
    if not items:
        run.stats = {"note": "解析结果中没有技术类评分点"}
        return

    text = "\n\n".join(f"【{ch.chapter_key} {ch.title}】\n{c}" for ch, c in chapters)[:50_000]
    item_block = "\n".join(
        f"[{it.item_key}] {it.item}（最高 {it.score} 分）评分标准：{it.criteria_original}" for it in items
    )
    if settings.sample_mode:
        from app.samples.check_fixtures import sample_scoring
        result = sample_scoring(items)
    else:
        result = chat_structured(
            SCORING_SYSTEM_PROMPT,
            f"# 技术评分点（逐点估分）\n{item_block}\n\n# 投标文件全文\n{text}",
            ScoreEstimateResult, layer="copilot", timeout=600,
        )

    existing = {
        e.scoring_item_id: e for e in
        db.query(ScoreEstimate).filter(ScoreEstimate.project_id == run.project_id).all()
    }
    item_by_key = {it.item_key: it for it in items}
    stats = {"items": len(items), "total_max": sum(it.score for it in items), "total_estimated": 0.0}
    for est in result.estimates:
        it = item_by_key.get(est.item_key)
        if it is None:
            continue
        score = max(0.0, min(est.estimated_score, it.score))  # 估分夹在 [0, 最高分]
        stats["total_estimated"] += score
        row = existing.get(it.id)
        if row is None:
            row = ScoreEstimate(project_id=run.project_id, scoring_item_id=it.id, max_score=it.score)
            db.add(row)
        # 只覆盖 AI 字段；手工修正（manual_score/note）与 adjusted/confirmed 状态保留
        row.max_score = it.score
        row.estimated_score = score
        row.evidence = est.evidence
        row.deduction_reasons = est.deduction_reasons
        row.improvement_advice = est.improvement_advice
        row.run_id = run.id
        if row.status == "ai":
            row.status = "ai"
    stats["total_estimated"] = round(stats["total_estimated"], 1)
    run.stats = stats
    db.flush()


# ---------------------------------------------------------------------------
# 编排入口
# ---------------------------------------------------------------------------

def execute_run(run_id: int) -> None:
    """worker 执行体：loading → 各检查器 → done/failed。"""
    db: Session = SessionLocal()
    try:
        run = db.get(CheckRun, run_id)
        if run is None or run.state != "running":
            return
        project = db.get(Project, run.project_id)
        if project is None or project.state != "wb_ready":
            _fail_run(db, run, f"项目状态 {project and project.state} 不允许执行检查")
            return
        chapters = _scope_chapters(db, run.project_id, run.chapter_keys or [])
        if not chapters:
            run.stats = {"note": "范围内没有正文章节"}
            run.state = "done"
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            return
        try:
            if run.check_type == "disqualification":
                run_disq_check(db, run, chapters)
            elif run.check_type == "typo":
                run_typo_check(db, run, chapters)
            elif run.check_type == "logic":
                run_logic_check(db, run, chapters)
            elif run.check_type == "scoring":
                run_scoring_check(db, run, chapters)
            else:
                raise ValueError(f"未知检查类型 {run.check_type}")
            run.state = "done"
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
        except Exception as e:  # noqa: BLE001
            _fail_run(db, run, f"{type(e).__name__}: {e}")
    finally:
        db.close()


def dispatch_check(project_id: int, check_type: str, chapter_keys: list[str], user_id: int | None) -> CheckRun:
    """建 run 行并派发（SYNC_TASKS 同步执行完再返回）。"""
    db: Session = SessionLocal()
    try:
        run = CheckRun(
            project_id=project_id, check_type=check_type,
            scope="chapters" if chapter_keys else "all", chapter_keys=chapter_keys,
            created_by=user_id,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
    finally:
        db.close()

    if settings.sync_tasks:
        execute_run(run.id)
    else:
        from app.worker import celery_app
        celery_app.send_task("app.worker.workbench_check", args=[run.id])
    return run
