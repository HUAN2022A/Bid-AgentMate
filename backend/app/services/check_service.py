"""自查服务：check_bid.py 的数据库适配版。

与 skill 版差异（软件化不改检查逻辑，只改数据源）：
- 章节来源 = chapter_versions 最新版本行（不再读 chapters/*.md 文件）
- 大纲/解析来源 = outline_snapshots + scoring_items/tech_requirements 表（不再读 yaml）
- 输出 = markdown 报告落存储 + 结构化摘要入库
- covers 注释/文件名 id/图注编号/图片路径四项 --fix 不适用（数据库无文件层），跳过
"""
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.storage import storage
from app.models.chapter import Chapter, ChapterVersion
from app.models.outline import OutlineSnapshot
from app.models.project import Project
from app.models.scoring_item import ScoringItemRow
from app.models.tech_requirement import TechRequirementRow


# 硬指标关键词：原文中的数字+单位片段（如 99.5%、400N、24个月）。run_check ★硬指标节与覆盖矩阵共用
HARD_KW_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|N|kN|kg|秒|天|个月|年|MPa|kV|mm|m\b)")


def extract_hard_keywords(text: str) -> list[str]:
    """原文 → 硬指标关键词（数字+单位片段）。无数字指标时返回空表（调用方决定退化策略）。"""
    return HARD_KW_RE.findall(text)


def find_keyword_hits(kws: list[str], content: str) -> list[str]:
    """返回在正文中出现的关键词子集（strip 后匹配，语义同原 ★硬指标节）。"""
    return [kw for kw in kws if kw.strip() in content]


def latest_contents(db: Session, project_id: int) -> dict[str, str]:
    """{chapter_key: 最新版本正文}（正文为空的章节不出现）。run_check 与覆盖矩阵共用。"""
    chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id)
        .order_by(Chapter.sort_order)
        .all()
    )
    out = {}
    for ch in chapters:
        v = (
            db.query(ChapterVersion)
            .filter(ChapterVersion.chapter_id == ch.id)
            .order_by(ChapterVersion.version_no.desc())
            .first()
        )
        if v and v.content_md.strip():
            out[ch.chapter_key] = v.content_md
    return out


def scoring_chapter_map(db: Session, project_id: int) -> dict[str, list[str]]:
    """{item_key: [chapter_key...]}（挂父章已展开到叶章）。

    来源合并（run_check 覆盖矩阵与覆盖热力图共用，唯一一份挂接逻辑）：
    - 先解析 chapters.scoring_keys（逗号分隔，同 copilot_service 的解析惯例）
    - 再合并大纲快照 tree 的挂接：挂父章按 k == o or k.startswith(o + ".") 展开到叶章列
      （修复演示库 outline_snapshots 为空时 run_check 误判「未挂章」的问题）
    """
    chapters = db.query(Chapter).filter(Chapter.project_id == project_id).all()
    leaf_keys = [ch.chapter_key for ch in chapters]
    mapping: dict[str, list[str]] = {}

    def _add(s: str, k: str):
        if k not in mapping.setdefault(s, []):
            mapping[s].append(k)

    for ch in chapters:  # 来源一：chapters.scoring_keys（确认大纲时固化）
        for s in (k.strip() for k in ch.scoring_keys.split(",")):
            if s:
                _add(s, ch.chapter_key)

    project = db.get(Project, project_id)  # 来源二：大纲快照 tree（版本取法同 run_check）
    snap = None
    if project is not None:
        snap = (
            db.query(OutlineSnapshot)
            .filter(OutlineSnapshot.project_id == project_id, OutlineSnapshot.version == project.outline_version)
            .first()
        )

    def _walk(ns):
        for n in ns or []:
            node_key = str(n.get("id"))
            for s in n.get("scoring_keys") or []:
                for k in leaf_keys:  # 挂父章 → 展开到叶章列
                    if k == node_key or k.startswith(node_key + "."):
                        _add(str(s), k)
            _walk(n.get("children") or [])

    if snap is not None:
        _walk(snap.tree.get("nodes", []))
    return mapping


def _leaf_keys(nodes: list[dict]) -> list[str]:
    out = []
    def walk(ns):
        for n in ns:
            if n.get("children"):
                walk(n["children"])
            else:
                out.append(str(n["id"]))
    walk(nodes)
    return out


def run_check(project_id: int) -> dict:
    """执行自查，报告落存储、摘要返回。状态边：draft_done → checking → draft_done（回退）。"""
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project is None or project.state not in ("draft_done", "checking", "exported"):
            return {"error": f"状态 {project and project.state} 不允许自查"}
        project.state = "checking"
        db.commit()

        tech_items = (
            db.query(ScoringItemRow)
            .filter(ScoringItemRow.project_id == project_id, ScoringItemRow.category == "技术")
            .all()
        )
        biz_items = (
            db.query(ScoringItemRow)
            .filter(ScoringItemRow.project_id == project_id, ScoringItemRow.category.in_(["商务", "价格", "资质"]))
            .all()
        )
        tech_reqs = (
            db.query(TechRequirementRow)
            .filter(TechRequirementRow.project_id == project_id)
            .all()
        )
        snap = (
            db.query(OutlineSnapshot)
            .filter(OutlineSnapshot.project_id == project_id, OutlineSnapshot.version == project.outline_version)
            .first()
        )
        outline_nodes = snap.tree.get("nodes", []) if snap else []
        contents = latest_contents(db, project_id)

        lines = [f"# 自查报告（{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC）\n"]

        # 一、评分点覆盖矩阵
        lines.append("## 一、评分点覆盖率矩阵\n")
        lines.append("| 评分项 | 分值 | 大纲挂章 | 覆盖状态 |")
        lines.append("|---|---|---|---|")
        score_to_chap = scoring_chapter_map(db, project_id)
        covered = 0
        for it in tech_items:
            ochaps = score_to_chap.get(it.item_key, [])
            hits = [k for k in ochaps if k in contents]  # 挂父章已在公共函数内展开到叶章
            if hits:
                status = "✅ 已响应"
                covered += 1
            elif ochaps:
                status = "⚠️ 大纲已挂章但无正文"
            else:
                status = "❌ 未挂章"
            lines.append(f"| {it.item_key} {it.item} | {it.score} | {'、'.join(ochaps) or '—'} | {status} |")
        lines.append("")

        # 二、★硬指标响应（关键词命中）
        lines.append("## 二、★/▲ 硬指标响应（关键词命中）\n")
        lines.append("| id | 要求摘要 | 命中章节 | 状态 |")
        lines.append("|---|---|---|---|")
        star_reqs = [r for r in tech_reqs if r.star]
        star_hit = 0
        for r in star_reqs:
            orig = r.requirement_original.strip().replace("\n", " ")
            kws = extract_hard_keywords(orig)
            if not kws:
                kws = [orig[:8]]  # ★硬指标清单特有兜底：无数字指标时取原文前 8 字
            hits = [k for k, txt in contents.items() if find_keyword_hits(kws, txt)]
            if hits:
                star_hit += 1
            lines.append(f"| {r.req_key} | {orig[:30]}… | {'、'.join(hits[:3]) or '—'} | {'✅' if hits else '❌ 未命中'} |")
        lines.append("")

        # 三、废标风险（技术卷相关，来自 yaml 快照的 disqualification）
        lines.append("## 三、废标风险清单（技术卷相关）\n")
        import yaml
        disq = []
        yaml_path = f"projects/{project_id}/tender/tender-analysis.yaml"
        if storage.exists(yaml_path):
            tender_yaml = yaml.safe_load(storage.get(yaml_path).decode("utf-8")) or {}
            disq = [d for d in tender_yaml.get("disqualification", []) if not d.get("manual")]
        lines.append("| id | 条款摘要 | 自查结果 |")
        lines.append("|---|---|---|")
        for d in disq:
            summ = (d.get("clause_original") or "").strip().replace("\n", " ")[:40]
            result = "需人工复核"
            if "偏离" in summ:
                result = "✅ 技术偏离表已编制" if any("偏离" in t for t in contents.values()) else "❌ 缺技术偏离表"
            elif "关键技术响应表" in summ:
                result = "✅ 关键技术响应表已编制" if any("关键技术响应表" in t for t in contents.values()) else "❌ 缺关键技术响应表"
            lines.append(f"| {d.get('id')} | {summ}… | {result} |")
        lines.append("")

        # 四、格式核对
        lines.append("## 四、格式核对\n")
        leaf = _leaf_keys(outline_nodes)
        lines.append(f"- 大纲叶章节数：{len(leaf)}；有正文章节数：{len(contents)}")
        price_hits = []
        for k, txt in contents.items():
            for m in re.finditer(r"(报价|投标价|总价款|人民币\s*\d)", txt):
                ctx = txt[max(0, m.start() - 10):m.start() + 12].replace("\n", " ")
                price_hits.append((k, m.group(), ctx))
        if price_hits:
            lines.append(f"- ⚠️ 检测到 {len(price_hits)} 处疑似报价信息（需人工确认是否为违约条款语境）:")
            for k, kw, ctx in price_hits[:10]:
                lines.append(f"  - {k}: 「…{ctx}…」")
        else:
            lines.append("- ✅ 未检测到报价信息混入")
        lines.append("")

        # 五、[待补]缺口
        lines.append("## 五、[待补]缺口清单\n")
        total_pending = 0
        for k, txt in contents.items():
            for m in re.findall(r"\[待补[^]]*\]", txt):
                total_pending += 1
                lines.append(f"- **{k}**：{m}")
        lines.append(f"\n共 {total_pending} 个待补项。\n")

        # 六、人工处理清单
        lines.append("## 六、人工处理清单（商务/价格/资质，技术卷范围外）\n")
        for it in biz_items:
            lines.append(f"- [{it.category}] {it.item}（{it.score}分）")
        lines.append("")

        report = "\n".join(lines)
        report_rel = f"projects/{project_id}/export/check-report.md"
        storage.put(report_rel, report.encode("utf-8"))

        project.state = "draft_done"  # 自查完回退，可反复自查
        db.commit()
        return {
            "report_path": report_rel,
            "tech_items": len(tech_items),
            "covered": covered,
            "star_reqs": len(star_reqs),
            "star_hit": star_hit,
            "pending_gaps": total_pending,
            "price_hits": len(price_hits),
        }
    finally:
        db.close()


def _clip(text: str, limit: int = 60) -> str:
    """单行化并封顶 limit 字（超出以 … 收尾）。"""
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _kw_evidence(kw: str, content: str, span: int = 15) -> str:
    """关键词 + 前后各 span 字上下文，整体封顶 60 字。"""
    k = kw.strip()
    i = content.find(k)
    return k if i < 0 else _clip(f"…{content[max(0, i - span):i + len(k) + span]}…")


def build_coverage(db: Session, project_id: int) -> dict:
    """构建评分点×章节覆盖矩阵（只读实时计算，不落库、不设状态门）。

    返回 {chapters, items, summary}：chapters 列（按 sort_order）、items 行（按 item_key），
    每行 cells 与 chapters 等长同序；格级三态 covered/partial/none + 证据摘要，
    判定语义见 docs/showcase/01-coverage-heatmap.md §2 决策表。
    """
    chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id)
        .order_by(Chapter.sort_order, Chapter.chapter_key)
        .all()
    )
    contents = latest_contents(db, project_id)
    score_map = scoring_chapter_map(db, project_id)
    items = (
        db.query(ScoringItemRow)
        .filter(ScoringItemRow.project_id == project_id)
        .order_by(ScoringItemRow.item_key)
        .all()
    )

    chapters_out = []
    for ch in chapters:
        v = (  # 列头字数取最新版本行（无版本记 0）
            db.query(ChapterVersion)
            .filter(ChapterVersion.chapter_id == ch.id)
            .order_by(ChapterVersion.version_no.desc())
            .first()
        )
        chapters_out.append(
            {
                "chapter_key": ch.chapter_key,
                "title": ch.title,
                "has_content": ch.chapter_key in contents,
                "word_count": v.word_count if v else 0,
            }
        )

    rank = {"none": 0, "partial": 1, "covered": 2}
    items_out = []
    summary = {"total": 0, "covered": 0, "partial": 0, "none": 0}
    for it in items:
        kws = extract_hard_keywords(it.criteria_original)
        linked = score_map.get(it.item_key, [])
        linked_set = set(linked)
        cells = []
        for cell_ch in chapters_out:
            key = cell_ch["chapter_key"]
            content = contents.get(key, "")
            hits = find_keyword_hits(kws, content) if kws and content else []
            if kws:  # 有数字指标：按命中数定格
                if not content:
                    status, evidence = "none", ""
                elif len(hits) == len(kws):
                    status, evidence = "covered", _kw_evidence(hits[0], content)
                elif hits:
                    status = "partial"
                    evidence = _clip(f"命中 {len(hits)}/{len(kws)}：{_kw_evidence(hits[0], content)}")
                elif key in linked_set:
                    status = "partial"
                    evidence = _clip("挂接本章但未命中硬指标关键词：" + "、".join(k.strip() for k in kws))
                else:
                    status, evidence = "none", ""
            else:  # 无数字指标：退化为挂接判定
                if key in linked_set and content:
                    status, evidence = "covered", "无硬指标关键词，按挂接+正文判定"
                elif key in linked_set:
                    status, evidence = "partial", "已挂接本章，正文未起草"
                else:
                    status, evidence = "none", ""
            cells.append({"chapter_key": key, "status": status, "hit_keywords": hits, "evidence": evidence})
        row_status = max((c["status"] for c in cells), key=lambda s: rank[s], default="none")
        summary[row_status] += 1
        summary["total"] += 1
        items_out.append(
            {
                "item_key": it.item_key,
                "item": it.item,
                "category": it.category,
                "score": it.score,
                "criteria_brief": _clip(it.criteria_original, 80),
                "linked_chapters": linked,
                "row_status": row_status,
                "cells": cells,
            }
        )
    return {"chapters": chapters_out, "items": items_out, "summary": summary}
