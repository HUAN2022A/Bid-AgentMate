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


def _latest_contents(db: Session, project_id: int) -> dict[str, str]:
    """{chapter_key: 最新版本正文}"""
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
        contents = _latest_contents(db, project_id)

        lines = [f"# 自查报告（{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC）\n"]

        # 一、评分点覆盖矩阵
        lines.append("## 一、评分点覆盖率矩阵\n")
        lines.append("| 评分项 | 分值 | 大纲挂章 | 覆盖状态 |")
        lines.append("|---|---|---|---|")
        score_to_chap: dict[str, list[str]] = {}
        def _collect(ns):
            for n in ns:
                for s in n.get("scoring_keys") or []:
                    score_to_chap.setdefault(str(s), []).append(str(n["id"]))
                _collect(n.get("children") or [])
        _collect(outline_nodes)
        covered = 0
        for it in tech_items:
            ochaps = score_to_chap.get(it.item_key, [])
            hits = [k for k in ochaps if k in contents]
            # 子章命中也算（评分点挂父章、正文在子章）
            if not hits:
                hits = [k for k in contents if any(k == o or k.startswith(o + ".") for o in ochaps)]
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
            # 关键词 = 原文中的数字+单位片段（如 99.5%、400N、24个月）
            kws = re.findall(r"\d+(?:\.\d+)?\s*(?:%|N|kN|kg|秒|天|个月|年|MPa|kV|mm|m\b)", orig)
            if not kws:
                kws = [orig[:8]]
            hits = [k for k, txt in contents.items() if any(kw.strip() in txt for kw in kws)]
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
