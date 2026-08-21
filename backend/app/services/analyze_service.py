"""招标文件 LLM 解析服务（阶段 2 核心）：多文件提取 → LLM 拆解 → 入库 → 大纲草稿。

契约对齐 bid-parse 的 tender-analysis.yaml（软件化不改契约）。
状态边：parsing → outline_pending | parse_failed（沿用 Q21）。
多文件（2026-08-21 定稿）：按 role 取全部已上传文件，分块组装上下文、按预算配比。
"""
import json
import sys
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.llm import LLMError, chat_structured  # noqa: E402
from app.core.storage import storage  # noqa: E402
from app.models.file_object import FileObject  # noqa: E402
from app.models.outline import OutlineDraft  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.models.scoring_item import ScoringItemRow  # noqa: E402
from app.models.tech_requirement import TechRequirementRow  # noqa: E402
from app.models.tender_file import TenderFile  # noqa: E402
from app.schemas.analysis import TenderAnalysisResult  # noqa: E402

from scripts.extract_docx import extract_docx_lines  # noqa: E402
from scripts.extract_pdf import extract_pdf_lines  # noqa: E402

# 上下文预算（字符）：按角色配比，评分/废标/商务密集在 main，★参数密集在 spec
BUDGET_MAIN = 60_000
BUDGET_SPEC = 40_000
BUDGET_ATTACHMENT = 10_000

ROLE_BLOCK_LABEL = {"main": "招标文件正文", "spec": "技术规范书", "attachment": "附件"}

SYSTEM_PROMPT = """你是招标文件解析专家。任务：从招标文件全文中提取技术标书撰写所需的全部依据，只提取不评价。

输入可能包含多个文件块，每块以【文件名｜角色】开头：招标文件正文（评分办法/废标/商务/格式）、技术规范书（★技术参数主来源）、附件。

提取规则（必须严格遵守）：
1. criteria_original / requirement_original / clause_original 必须原文逐字照抄（保留 ★▲☆ 和序号），禁止改写、总结、转述。
2. 每条评分项打 category 分卷标签：技术|商务|价格|资质|其他。评分办法自带分组按分组；无分组按内容判断。
3. 商务/价格/资质项浅提取：只留 id/category/item/score/criteria_original，不深加工。
4. 资格与门槛最容易漏：限价、保证金、业绩要求、职称、资质、联合体条款必须逐条提取到 qualification。
5. 付款/违约/质保等商务要点提取到 commercial_notes（浅提取，人工处理）。
6. 废标/无效投标/否决条款逐条提取到 disqualification，商务/资质类标 manual=true。
7. 技术卷格式要求（字体、份数、密封、页码、目录等）提取到 format_requirements。
8. 招标文件明确要求技术文件包含的内容提取到 structure_requirements。
9. tech_requirements 主要从技术规范书逐条提取：★/☆/▲ 号条款一条不漏，一般技术参数按主题归并（同主题多条参数合为一条，原文保留全部参数值）。
10. scoring.items 是输出的核心：评分办法里的每一条评分项都必须出现，即使分值/名称需要推断也先提取再在 note 标注；items 绝不允许为空数组——招标文件正文块中必有"评标办法/评分标准"章节。
11. 拿不准的值留空字符串，不猜。"""

OUTLINE_SYSTEM_PROMPT = """你是技术标书大纲设计专家。任务：根据招标文件解析结果设计技术文件章节树。

设计规则（必须严格遵守）：
1. 骨架 = structure_requirements 顺序，章名不动；无结构要求时按行业惯例（项目理解/总体方案/实施方案/质量保障/售后服务等）。
2. 每个技术类评分点必须挂到唯一章节（scoring_keys 引用评分项 id）。
3. 目标字数按评分分值加权分配，技术项总分值越大章节字数越多；全书一般 5-8 万字。
4. 章节 id 用层级编号（1、1.1、1.1.1），稳定不重复。
5. 尾部固定两章：关键技术响应表、技术偏离表（不挂评分点）。
6. 只输出章节树，不写正文。"""


def _truncate(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    head = budget * 2 // 3
    tail = budget - head
    return text[:head] + "\n\n……[中间部分省略]……\n\n" + text[-tail:]


# 评分章节关键词：命中段落在截断时必须保留（金标准教训：LLM 只拿到"见评标办法前附表"
# 的引用句而拿不到前附表本体时，会输出空评分项或引用句伪评分项）
_SCORING_KEYWORDS = ["评标办法", "评分办法", "评分标准", "评分细则", "评审因素", "分值构成"]


def _smart_truncate(text: str, budget: int) -> str:
    """截断时优先保留评分章节：含评分关键词的行强制保留，其余按头尾配比。"""
    if len(text) <= budget:
        return text
    lines = text.split("\n")
    keep_idx = {
        i for i, l in enumerate(lines) if any(k in l for k in _SCORING_KEYWORDS)
    }
    # 命中行前后各扩 2 行（评分表常跨行）
    expand = set()
    for i in keep_idx:
        expand.update(range(max(0, i - 2), min(len(lines), i + 3)))
    kept = "\n".join(l for i, l in enumerate(lines) if i in expand)
    rest_budget = budget - len(kept) - 100
    if rest_budget <= 0:
        return _truncate(kept, budget)
    rest = "\n".join(l for i, l in enumerate(lines) if i not in expand)
    head = rest_budget * 2 // 3
    tail = rest_budget - head
    rest_trunc = rest[:head] + "\n……[中间部分省略]……\n" + rest[-tail:]
    return kept + "\n\n" + rest_trunc


def _extract_one(tender: TenderFile, fo: FileObject) -> tuple[str, dict]:
    """单文件提取全文（幂等：已提取则复用）。"""
    if tender.extracted_text_path and storage.exists(tender.extracted_text_path):
        return storage.get(tender.extracted_text_path).decode("utf-8"), json.loads(tender.extract_stats or "{}")
    src = storage.abspath(fo.relative_path)
    if tender.file_type == "pdf":
        lines, stats = extract_pdf_lines(str(src))
    else:
        lines, stats = extract_docx_lines(str(src))
    text = "\n".join(lines)
    text_rel = f"projects/{tender.project_id}/tender/extracted-{tender.role}-{tender.id}.txt"
    storage.put(text_rel, text.encode("utf-8"))
    tender.extracted_text_path = text_rel
    tender.extract_stats = json.dumps(stats, ensure_ascii=False)
    return text, stats


def run_analyze(project_id: int) -> None:
    """完整解析：全文件提取 → LLM 拆解 → 入库 + 大纲草稿。Celery 任务或同步调用共用。"""
    db: Session = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project is None or project.state != "parsing":
            return
        tenders = (
            db.query(TenderFile)
            .filter(TenderFile.project_id == project_id)
            .order_by(TenderFile.id)
            .all()
        )
        mains = [t for t in tenders if t.role == "main"]
        if not mains:
            _fail(db, project, "未找到招标文件正文（role=main）")
            return

        try:
            # 1. 全文件提取 + 按角色分块组装（main 取最新一份，spec/attachment 全取）
            budgets = {"main": BUDGET_MAIN, "spec": BUDGET_SPEC, "attachment": BUDGET_ATTACHMENT}
            blocks = []
            scanned_warn = False
            seen_roles: dict[str, int] = {}
            ordered = [mains[-1]] + [t for t in tenders if t.role != "main"]
            for tender in ordered:
                fo = db.get(FileObject, tender.file_object_id)
                text, stats = _extract_one(tender, fo)
                scanned_warn = scanned_warn or stats.get("maybe_scanned", False)
                seen_roles[tender.role] = seen_roles.get(tender.role, 0) + 1
                label = ROLE_BLOCK_LABEL.get(tender.role, tender.role)
                blocks.append(
                    f"【{fo.original_name}｜{label}】\n" + _smart_truncate(text, budgets.get(tender.role, BUDGET_ATTACHMENT))
                )
            db.flush()
            fulltext = "\n\n".join(blocks)

            # 2. LLM 拆解（Q26 契约）
            result = chat_structured(SYSTEM_PROMPT, fulltext, TenderAnalysisResult)
            # 业务校验（Q26：校验失败转重试而非入库）：评分项为空或分值全零 = 核心契约未满足
            def _scoring_invalid(r: TenderAnalysisResult) -> bool:
                return not r.scoring.items or all(it.score == 0 for it in r.scoring.items)

            if _scoring_invalid(result):
                retry_prompt = (
                    "你上次的提取结果评分项为空或分值全为 0，这不满足要求。"
                    "招标文件正文中必有'评标办法/评分标准/评分细则'章节（含评分因素、满分值、评分标准的表格），"
                    "请重新阅读并逐条提取全部评分项（含技术/商务/价格各卷，每条带真实分值），"
                    "只输出修正后的完整 JSON 对象。\n\n" + fulltext
                )
                result = chat_structured(SYSTEM_PROMPT, retry_prompt, TenderAnalysisResult)
            if _scoring_invalid(result):
                raise LLMError("LLM 两次提取评分项均为空或分值全零，转人工核对")

            # 软告警：技术卷分值明显偏低时记录（不阻断，人工在大纲页核对）
            soft_warn = ""
            tech_total = sum(it.score for it in result.scoring.items if it.category == "技术")
            if 0 < tech_total < 40:
                soft_warn = f"WARN: 技术卷评分合计仅 {tech_total} 分，可能有评分项漏提，请人工核对评分办法"
            _persist_analysis(db, project, mains[-1], result)

            # 3. LLM 生成大纲草稿（Q27：草稿 + AI 原始件同存）
            outline_tree = _generate_outline(result)
            draft = db.query(OutlineDraft).filter(OutlineDraft.project_id == project_id).first()
            if draft is None:
                draft = OutlineDraft(project_id=project_id, tree=outline_tree, ai_raw_tree=outline_tree)
                db.add(draft)
            else:
                draft.tree = outline_tree
                draft.ai_raw_tree = outline_tree

            project.state = "outline_pending"
            project.parse_error = soft_warn
            if scanned_warn:
                project.parse_error = (soft_warn + "；" if soft_warn else "") + "部分文件平均每页字符偏少，可能是扫描件，请人工核对提取质量"
            db.commit()
        except LLMError as e:
            _fail(db, project, str(e))
        except Exception as e:
            _fail(db, project, f"{type(e).__name__}: {e}")
    finally:
        db.close()


def _fail(db: Session, project: Project, msg: str) -> None:
    project.state = "parse_failed"
    project.parse_error = msg[:2000]
    db.commit()


def _persist_analysis(db: Session, project: Project, main_tender: TenderFile, r: TenderAnalysisResult) -> None:
    """解析结果入库 + 落 yaml 快照（与 skill 版 tender-analysis.yaml 同构，供人工核对/导出）。"""
    db.query(ScoringItemRow).filter(ScoringItemRow.project_id == project.id).delete()
    db.query(TechRequirementRow).filter(TechRequirementRow.project_id == project.id).delete()

    for it in r.scoring.items:
        db.add(ScoringItemRow(
            project_id=project.id, item_key=it.id, category=it.category, item=it.item,
            score=it.score, criteria_original=it.criteria_original, location=it.location,
            response_hint=it.response_hint, note=it.note,
        ))
    for t in r.tech_requirements:
        db.add(TechRequirementRow(
            project_id=project.id, req_key=t.id, star=t.star,
            requirement_original=t.requirement_original, location=t.location,
        ))

    if r.project_name and not project.name:
        project.name = r.project_name
    if r.tender_no and not project.tender_no:
        project.tender_no = r.tender_no

    yaml_rel = f"projects/{project.id}/tender/tender-analysis.yaml"
    storage.put(yaml_rel, yaml.dump(
        r.model_dump(mode="json"), allow_unicode=True, sort_keys=False
    ).encode("utf-8"))
    main_tender.analysis_yaml_path = yaml_rel
    db.flush()


def _generate_outline(r: TenderAnalysisResult) -> dict:
    """LLM 生成大纲章节树。"""
    from app.schemas.outline import OutlineTree

    summary = {
        "project_name": r.project_name,
        "scoring": r.scoring.model_dump(mode="json"),
        "tech_requirements": [t.model_dump(mode="json") for t in r.tech_requirements],
        "structure_requirements": [s.model_dump(mode="json") for s in r.structure_requirements],
        "format_requirements": [f.model_dump(mode="json") for f in r.format_requirements],
    }
    tree = chat_structured(
        OUTLINE_SYSTEM_PROMPT,
        "招标文件解析结果如下：\n" + json.dumps(summary, ensure_ascii=False, indent=2),
        OutlineTree,
    )
    return tree.model_dump(mode="json")


def dispatch_analyze(project_id: int) -> str:
    if settings.sync_tasks:
        run_analyze(project_id)
        return "sync"
    from app.worker import celery_app

    celery_app.send_task("app.worker.analyze_tender", args=[project_id])
    return "celery"
