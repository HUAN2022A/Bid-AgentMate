"""导出服务：export_docx.py 的数据库适配版。

章节来源 = chapter_versions 最新版本行；大纲一级章标题 = outline_snapshots；
渲染逻辑（样式/封面/目录/表格/[待补]高亮/标题层级映射）直接复用 scripts/export_docx.py。
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from docx import Document  # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: E402
from docx.shared import Pt  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.core.storage import storage  # noqa: E402
from app.models.chapter import Chapter, ChapterVersion  # noqa: E402
from app.models.outline import OutlineSnapshot  # noqa: E402
from app.models.project import Project  # noqa: E402

from scripts.export_docx import (  # noqa: E402
    add_page_number_footer,
    add_toc,
    chapter_sort_key,
    render_markdown,
    set_cjk_font,
    set_update_fields_on_open,
    setup_styles,
)


def _chapter_sort(key: str):
    return chapter_sort_key(f"{key}-x.md")


def build_export_doc(project_id: int, db: Session):
    """组装 docx 文档对象（导出与预览共用）。返回 (doc, meta) 或 (None, error)。"""
    project = db.get(Project, project_id)
    if project is None:
        return None, {"error": "项目不存在"}

    chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id)
        .all()
    )
    contents: dict[str, tuple[str, str]] = {}  # key -> (title, content)
    total_words = 0
    pending = 0
    for ch in chapters:
        v = (
            db.query(ChapterVersion)
            .filter(ChapterVersion.chapter_id == ch.id)
            .order_by(ChapterVersion.version_no.desc())
            .first()
        )
        if v and v.content_md.strip():
            contents[ch.chapter_key] = (ch.title, v.content_md)
            total_words += v.word_count
            pending += v.content_md.count("[待补")
    if not contents:
        return None, {"error": "尚无章节正文可导出"}

    snap = (
        db.query(OutlineSnapshot)
        .filter(OutlineSnapshot.project_id == project_id, OutlineSnapshot.version == project.outline_version)
        .first()
    )
    parent_titles = {}
    if snap:
        for n in snap.tree.get("nodes", []):
            parent_titles[str(n["id"])] = n.get("title", "")

    doc = Document()
    setup_styles(doc)
    add_page_number_footer(doc)
    set_update_fields_on_open(doc)

    # 封面
    for _ in range(4):
        doc.add_paragraph()
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("投 标 文 件"); r.bold = True; r.font.size = Pt(36); set_cjk_font(r, "黑体")
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("（技术文件）"); r.bold = True; r.font.size = Pt(22); set_cjk_font(r, "黑体")
    for _ in range(3):
        doc.add_paragraph()
    for label, val in [("项目名称", project.name), ("招标编号", project.tender_no), ("投标人", "（盖章）"), ("日期", "")]:
        p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(f"{label}：{val}"); r.font.size = Pt(14); set_cjk_font(r)
    doc.add_page_break()

    # 目录
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("目  录"); r.bold = True; r.font.size = Pt(16); set_cjk_font(r, "黑体")
    add_toc(doc)
    doc.add_page_break()

    # 章节（按大纲 id 数值排序；子节父章无正文时补发父章标题）
    emitted_parents: set[str] = set()
    keys = sorted(contents.keys(), key=_chapter_sort)
    top_keys = {k for k in keys if "." not in k}
    for key in keys:
        title, text = contents[key]
        if "." in key:
            parent = key.split(".")[0]
            if parent not in emitted_parents and parent not in top_keys:
                ptitle = parent_titles.get(parent, "")
                doc.add_heading(f"{parent} {ptitle}".strip(), level=1)
                emitted_parents.add(parent)
        render_markdown(doc, text, base_id=key, ws=None)
        doc.add_page_break()

    meta = {
        "chapters": len(contents),
        "total_words": total_words,
        "pending_gaps": pending,
        "project_name": project.name,
        "tender_no": project.tender_no,
    }
    return doc, meta


def run_export_preview(project_id: int) -> dict:
    """导出前预览：结构摘要（章节清单/字数/待补/样式说明），不生成文件。"""
    db: Session = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project is None or project.state not in ("draft_done", "checking", "exported"):
            return {"error": f"状态 {project and project.state} 不允许导出预览"}
        chapters = (
            db.query(Chapter)
            .filter(Chapter.project_id == project_id)
            .order_by(Chapter.sort_order)
            .all()
        )
        items = []
        total_words = 0
        pending = 0
        for ch in chapters:
            v = (
                db.query(ChapterVersion)
                .filter(ChapterVersion.chapter_id == ch.id)
                .order_by(ChapterVersion.version_no.desc())
                .first()
            )
            if v and v.content_md.strip():
                total_words += v.word_count
                p_cnt = v.content_md.count("[待补")
                pending += p_cnt
                items.append({
                    "key": ch.chapter_key, "title": ch.title,
                    "words": v.word_count, "pending": p_cnt,
                })
        if not items:
            return {"error": "尚无章节正文可导出"}
        return {
            "project_name": project.name,
            "tender_no": project.tender_no,
            "chapters": items,
            "total_words": total_words,
            "pending_gaps": pending,
            "style_notes": [
                "封面：投标文件（技术文件）+ 项目名称/招标编号",
                "目录：自动目录（打开文档自动更新页码），宋体小四",
                "一级章标题：宋体四号加粗；二级及以下：黑体",
                "正文：宋体小四，1.3 倍行距，首行缩进 2 字符",
                "表格：Table Grid，表头加粗，表格文字五号",
                "[待补]：黄色高亮",
            ],
        }
    finally:
        db.close()


def run_export(project_id: int) -> dict:
    """合成 docx 终稿落存储。状态边：draft_done/checking → exported。"""
    db: Session = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project is None or project.state not in ("draft_done", "checking", "exported"):
            return {"error": f"状态 {project and project.state} 不允许导出"}
        doc, meta = build_export_doc(project_id, db)
        if doc is None:
            return meta
        export_rel = f"projects/{project_id}/export/技术文件.docx"
        tmp = storage.abspath(export_rel)
        tmp.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(tmp))

        project.state = "exported"
        db.commit()
        return {
            "export_path": export_rel,
            "chapters": meta["chapters"],
            "total_words": meta["total_words"],
            "pending_gaps": meta["pending_gaps"],
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 标书工作台导出（润色/修复后的投标文件 docx + 检查报告 md）
# ---------------------------------------------------------------------------

WORKBENCH_EXPORT_REL = "projects/{pid}/export/投标文件-工作台版.docx"


def _normalize_wb_tables(md: str) -> str:
    """投标导入的表格行是「a | b」（extract_docx_structured 格式，无首尾竖线），
    render_markdown 只认标准 md 表格——导出前归一为「| a | b |」行。"""
    out: list[str] = []
    in_table = False
    for ln in md.split("\n"):
        s = ln.strip()
        if s == "[表格]":
            in_table = True
            continue
        if s == "[/表格]":
            in_table = False
            continue
        if in_table and s:
            out.append("| " + s.strip().strip("|").strip() + " |")
        else:
            out.append(ln)
    return "\n".join(out)


def _wb_cover(doc: Document, project: Project) -> None:
    for _ in range(4):
        doc.add_paragraph()
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("投 标 文 件"); r.bold = True; r.font.size = Pt(36); set_cjk_font(r, "黑体")
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("（商务技术文件 · 工作台修订版）"); r.bold = True; r.font.size = Pt(22); set_cjk_font(r, "黑体")
    for _ in range(3):
        doc.add_paragraph()
    for label, val in [("项目名称", project.name), ("招标编号", project.tender_no), ("投标人", "（盖章）"), ("日期", "")]:
        p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(f"{label}：{val}"); r.font.size = Pt(14); set_cjk_font(r)
    doc.add_page_break()


def build_workbench_doc(project_id: int, db: Session):
    """工作台导出：按投标目录树（doc_kind=bid 快照）递归渲染——非叶节点出标题、
    叶节点出润色/修复后的最新正文，保真原文档层级结构。"""
    project = db.get(Project, project_id)
    if project is None:
        return None, {"error": "项目不存在"}
    chapters = db.query(Chapter).filter(Chapter.project_id == project_id).all()
    contents: dict[str, tuple[str, str]] = {}
    total_words = 0
    for ch in chapters:
        v = (
            db.query(ChapterVersion)
            .filter(ChapterVersion.chapter_id == ch.id)
            .order_by(ChapterVersion.version_no.desc())
            .first()
        )
        if v and v.content_md.strip():
            contents[ch.chapter_key] = (ch.title, _normalize_wb_tables(v.content_md))
            total_words += v.word_count
    if not contents:
        return None, {"error": "尚无章节正文可导出"}

    snap = (
        db.query(OutlineSnapshot)
        .filter(
            OutlineSnapshot.project_id == project_id,
            OutlineSnapshot.doc_kind == "bid",
            OutlineSnapshot.version == project.bid_outline_version,
        )
        .first()
    )
    tree = snap.tree.get("nodes", []) if snap else []

    doc = Document()
    setup_styles(doc)
    add_page_number_footer(doc)
    set_update_fields_on_open(doc)
    _wb_cover(doc, project)
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("目  录"); r.bold = True; r.font.size = Pt(16); set_cjk_font(r, "黑体")
    add_toc(doc)
    doc.add_page_break()

    emitted = 0

    def walk(nodes: list[dict], depth: int):
        nonlocal emitted
        for n in nodes:
            nid = str(n.get("id", ""))
            children = n.get("children") or []
            if children:
                doc.add_heading(f"{nid} {n.get('title', '')}".strip(), level=min(depth, 4))
                walk(children, depth + 1)
            elif nid in contents:
                title, text = contents[nid]
                render_markdown(doc, text, base_id=nid, ws=None)
                doc.add_page_break()
                emitted += 1

    walk(tree, 1)
    if emitted == 0:
        # 快照缺失/结构不匹配时退化：按 sort_order 平铺叶章
        for ch in sorted(chapters, key=lambda c: _chapter_sort(c.chapter_key)):
            if ch.chapter_key in contents:
                title, text = contents[ch.chapter_key]
                render_markdown(doc, text, base_id=ch.chapter_key, ws=None)
                doc.add_page_break()
                emitted += 1

    return doc, {"chapters": emitted, "total_words": total_words,
                 "pending_gaps": sum(t.count("[待补") for _, t in contents.values())}


def run_workbench_export(project_id: int) -> dict:
    """工作台导出入口：wb_ready 即可反复导出（导出不改状态，正文以最新版本为准）。"""
    db: Session = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project is None or project.mode != "workbench" or project.state != "wb_ready":
            return {"error": f"状态 {project and project.state} 不允许导出（须工作台就绪）"}
        doc, meta = build_workbench_doc(project_id, db)
        if doc is None:
            return meta
        export_rel = WORKBENCH_EXPORT_REL.format(pid=project_id)
        tmp = storage.abspath(export_rel)
        tmp.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(tmp))
        return {
            "export_path": export_rel,
            "chapters": meta["chapters"],
            "total_words": meta["total_words"],
            "pending_gaps": meta["pending_gaps"],
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        db.close()


def build_workbench_check_report(project_id: int) -> tuple[str, str]:
    """检查报告 markdown（四类检查最新 run 的统计与发现 + 模拟评分）。
    返回 (storage 相对路径, 报告文本)；空项目返回错误信息在文本首行。"""
    from app.models.workbench import CheckRun, Finding, ScoreEstimate
    from app.models.scoring_item import ScoringItemRow

    db: Session = SessionLocal()
    try:
        project = db.get(Project, project_id)
        lines = [
            f"# 标书检查报告（工作台）",
            f"- 项目：{project.name if project else project_id}（招标编号 {project.tender_no if project else ''}）",
            f"- 生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC",
            "- 说明：检查发现可能只是待人工确认的风险，不等于问题成立；技术评分为模拟估分，不代表正式评标结果。",
            "",
        ]
        type_names = {"disqualification": "废标项检查", "typo": "错别字检查",
                      "logic": "逻辑谬误检查", "scoring": "技术模拟评分"}
        sev_names = {"high": "高", "medium": "中", "low": "低"}
        status_names = {"pending": "待确认", "confirmed": "属实", "dismissed": "误报", "fixed": "已修复"}

        for ctype in ("disqualification", "typo", "logic"):
            run = (
                db.query(CheckRun)
                .filter(CheckRun.project_id == project_id, CheckRun.check_type == ctype)
                .order_by(CheckRun.id.desc())
                .first()
            )
            lines.append(f"## {type_names[ctype]}")
            if run is None:
                lines.append("尚未执行。\n")
                continue
            stats = run.stats or {}
            parts = [f"run #{run.id} · {run.state}"]
            if ctype == "disqualification" and "clauses" in stats:
                parts.append(f"条款 {stats['clauses']}：通过 {stats.get('pass', 0)} · "
                             f"风险 {stats.get('risk', 0)} · 需人工 {stats.get('needs_review', 0)}")
            lines.append("- " + " · ".join(parts))
            findings = (
                db.query(Finding)
                .filter(Finding.run_id == run.id)
                .order_by(Finding.severity.desc(), Finding.id)
                .all()
            )
            if not findings:
                lines.append("- 未产生检查发现（或全部通过）。\n")
                continue
            lines.append("")
            lines.append("| 章节 | 严重度 | 证据/依据 | 分析与建议 | 状态 |")
            lines.append("|---|---|---|---|---|")
            for f in findings:
                basis = (f.tender_basis or f.bid_evidence or "—").replace("\n", " ").replace("|", "\\|")[:60]
                ans = f"{f.analysis}<br>建议：{f.suggestion}".replace("\n", " ").replace("|", "\\|")[:150]
                lines.append(f"| {f.chapter_key or '全局'} | {sev_names.get(f.severity, f.severity)} "
                             f"| {basis} | {ans} | {status_names.get(f.confirm_status, f.confirm_status)} |")
            lines.append("")

        lines.append("## 技术模拟评分（非正式评标结果）")
        rows = (
            db.query(ScoreEstimate, ScoringItemRow)
            .join(ScoringItemRow, ScoreEstimate.scoring_item_id == ScoringItemRow.id)
            .filter(ScoreEstimate.project_id == project_id)
            .order_by(ScoringItemRow.item_key)
            .all()
        )
        if not rows:
            lines.append("尚未估分。\n")
        else:
            total = sum((e.manual_score if e.manual_score is not None else e.estimated_score) for e, _ in rows)
            max_total = sum(e.max_score for e, _ in rows)
            lines.append(f"合计 **{total}/{max_total}**（修正分优先）\n")
            lines.append("| 评分点 | 满分 | 得分 | 扣分理由 |")
            lines.append("|---|---|---|---|")
            for e, i in rows:
                score = e.manual_score if e.manual_score is not None else e.estimated_score
                ded = (e.deduction_reasons or "—").replace("\n", " ").replace("|", "\\|")[:100]
                lines.append(f"| {i.item_key} {i.item} | {e.max_score} | {score} | {ded} |")

        text = "\n".join(lines)
        rel = f"projects/{project_id}/export/workbench-check-report.md"
        storage.put(rel, text.encode("utf-8"))
        return rel, text
    finally:
        db.close()
