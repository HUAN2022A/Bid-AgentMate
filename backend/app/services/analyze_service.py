"""招标文件 LLM 解析服务（阶段 2 核心）：提取全文 → LLM 拆解 → 入库 → 大纲草稿。

契约对齐 bid-parse 的 tender-analysis.yaml（软件化不改契约）。
状态边：parsing → outline_pending | parse_failed（沿用 Q21）。
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

# LLM 上下文预算：招标全文常超 100K 字符，超出时截头去尾保留评分/需求密集段
MAX_CONTEXT_CHARS = 80_000

SYSTEM_PROMPT = """你是招标文件解析专家。任务：从招标文件全文中提取技术标书撰写所需的全部依据，只提取不评价。

提取规则（必须严格遵守）：
1. criteria_original / requirement_original / clause_original 必须原文逐字照抄（保留 ★▲ 和序号），禁止改写、总结、转述。
2. 每条评分项打 category 分卷标签：技术|商务|价格|资质|其他。评分办法自带分组按分组；无分组按内容判断。
3. 商务/价格/资质项浅提取：只留 id/category/item/score/criteria_original，不深加工。
4. 资格与门槛最容易漏：限价、保证金、业绩要求、职称、资质、联合体条款必须逐条提取到 qualification。
5. 付款/违约/质保等商务要点提取到 commercial_notes（浅提取，人工处理）。
6. 废标/无效投标/否决条款逐条提取到 disqualification，商务/资质类标 manual=true。
7. 技术卷格式要求（字体、份数、密封、页码、目录等）提取到 format_requirements。
8. 招标文件明确要求技术文件包含的内容提取到 structure_requirements。
9. 拿不准的值留空字符串，不猜。"""

OUTLINE_SYSTEM_PROMPT = """你是技术标书大纲设计专家。任务：根据招标文件解析结果设计技术文件章节树。

设计规则（必须严格遵守）：
1. 骨架 = structure_requirements 顺序，章名不动；无结构要求时按行业惯例（项目理解/总体方案/实施方案/质量保障/售后服务等）。
2. 每个技术类评分点必须挂到唯一章节（scoring_keys 引用评分项 id）。
3. 目标字数按评分分值加权分配，技术项总分值越大章节字数越多；全书一般 5-8 万字。
4. 章节 id 用层级编号（1、1.1、1.1.1），稳定不重复。
5. 尾部固定两章：关键技术响应表、技术偏离表（不挂评分点）。
6. 只输出章节树，不写正文。"""


def _truncate(text: str) -> str:
    if len(text) <= MAX_CONTEXT_CHARS:
        return text
    head = MAX_CONTEXT_CHARS * 2 // 3
    tail = MAX_CONTEXT_CHARS - head
    return text[:head] + "\n\n……[中间部分省略]……\n\n" + text[-tail:]


def run_analyze(project_id: int) -> None:
    """完整解析：提取（若未做）→ LLM 拆解 → 入库 + 大纲草稿。Celery 任务或同步调用共用。"""
    db: Session = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project is None or project.state != "parsing":
            return
        tender = (
            db.query(TenderFile)
            .filter(TenderFile.project_id == project_id)
            .order_by(TenderFile.id.desc())
            .first()
        )
        if tender is None:
            _fail(db, project, "未找到招标文件")
            return

        try:
            # 1. 提取全文（幂等：已提取则复用）
            if tender.extracted_text_path and storage.exists(tender.extracted_text_path):
                fulltext = storage.get(tender.extracted_text_path).decode("utf-8")
            else:
                src = storage.abspath(db.get(FileObject, tender.file_object_id).relative_path)
                if tender.file_type == "pdf":
                    lines, stats = extract_pdf_lines(str(src))
                else:
                    lines, stats = extract_docx_lines(str(src))
                fulltext = "\n".join(lines)
                text_rel = f"projects/{project_id}/tender/extracted.txt"
                storage.put(text_rel, fulltext.encode("utf-8"))
                tender.extracted_text_path = text_rel
                tender.extract_stats = json.dumps(stats, ensure_ascii=False)
                db.flush()

            # 2. LLM 拆解（Q26 契约）
            result = chat_structured(SYSTEM_PROMPT, _truncate(fulltext), TenderAnalysisResult)
            _persist_analysis(db, project, tender, result)

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
            project.parse_error = ""
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


def _persist_analysis(db: Session, project: Project, tender: TenderFile, r: TenderAnalysisResult) -> None:
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
    tender.analysis_yaml_path = yaml_rel
    db.flush()


def _generate_outline(r: TenderAnalysisResult) -> dict:
    """LLM 生成大纲章节树。输出契约内联（树形递归结构不宜 Pydantic 深套，用 dict + 校验）。"""
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
