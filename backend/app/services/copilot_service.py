"""章节内 Copilot 服务（Q8 段落级重写落地）：上下文配方 → LLM 整段重写 → 软校验 → 打点。

设计依据（调研定稿）：
- 输出契约 = 处理后的整段 markdown（aider 实测 whole 格式应用最可靠），应用走编辑器选区替换，不做文本匹配
- 红线继承 DRAFT_SYSTEM_PROMPT：公司事实只准素材卡、响应不低于要求、禁报价
- 校验分级：硬校验只有"非空"（schema min_length → 喂错重试）；长度/报价为软校验，只提示不阻断
- 每次生成落 copilot_actions 行，前端应用时打 applied（应用率 = 北极星指标）
"""
import json
import re
import time
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.llm import chat_structured, resolve_llm
from app.models.chapter import Chapter, ChapterVersion
from app.models.copilot import CopilotAction
from app.models.material import Material, MaterialSnapshot
from app.models.scoring_item import ScoringItemRow
from app.models.tech_requirement import TechRequirementRow
from app.schemas.copilot import CopilotRequest, CopilotResponse, CopilotResult
from app.services.ingest_service import search_materials

PRICE_PATTERN = re.compile(r"(报价|投标价|总价款|人民币\s*\d)")  # 与 check_service 同源
MATERIAL_LIMIT = 3
NEIGHBOR_LIMIT = 800  # 前后文每块最多注入字符数


@dataclass(frozen=True)
class ActionSpec:
    label: str
    task: str  # 给模型的任务描述
    constraint: str  # 动作特有硬约束（第 6 条）
    needs_selection: bool = True
    inject_scoring: bool = False  # 注入本章挂接的评分标准原文
    inject_materials: bool = False
    inject_neighbors: bool = False  # 注入前后文
    needs_requirement: bool = False  # star_response：必须指定技术需求
    ratio: tuple[float, float] | None = None  # 软校验：输出/输入字符比预期区间
    chapter_input: bool = False  # 工作台整章动作：服务端自取最新版本全文为输入
    max_tokens: int | None = None  # 覆盖环节默认 max_tokens（整章输出比段落长得多）
    timeout_seconds: int | None = None  # 覆盖环节默认超时（整章生成耗时更长）


ACTIONS: dict[str, ActionSpec] = {
    "rewrite": ActionSpec(
        label="重写",
        task="按用户指令重写选中段落；无指令时提升其专业性、具体度与说服力",
        constraint="忠实保留原段落的全部事实与技术要点，不新增素材卡以外的公司事实",
        inject_scoring=True, inject_materials=True, inject_neighbors=True, ratio=(0.3, 3.0),
    ),
    "expand": ActionSpec(
        label="扩写",
        task="扩写选中段落：细化措施、补充实施步骤与保障手段，使论述更充实",
        constraint="扩写限于对既有方案的展开说明，新增公司事实只准引用素材卡，无支撑处标 [待补：xxx]",
        inject_scoring=True, inject_materials=True, inject_neighbors=True, ratio=(1.2, 4.0),
    ),
    "compress": ActionSpec(
        label="压缩",
        task="压缩选中段落：删除冗余与空话，保留全部技术要点",
        constraint="不得丢失任何 ★/☆/▲ 响应要素与具体技术参数值",
        ratio=(0.2, 0.85),
    ),
    "align_scoring": ActionSpec(
        label="对齐评分点",
        task="重写选中段落，使指定评分标准的每个评分要素都有明确、可逐条对照的响应表述",
        constraint="评分标准原文中的每个要素必须在段落中有显式对应的响应句（可分点列出），响应值不低于招标要求",
        inject_scoring=True, inject_materials=True, ratio=(0.5, 3.0),
    ),
    "tabulate": ActionSpec(
        label="表格化",
        task="把选中段落中的散点参数、指标或对比信息整理为 markdown 表格，表格前保留一句引导语",
        constraint="表格内容只能来自原段落，不得补充或推断任何数值",
        ratio=(0.3, 2.5),
    ),
    "star_response": ActionSpec(
        label="★条款响应",
        task="为指定的技术要求生成「招标要求 vs 我方响应」对照段：一句引导语 + 两列 markdown 表格（招标要求 | 我方响应）+ 必要的简短说明",
        constraint="我方响应值不得低于招标要求；涉及我方能力/业绩的事实只准引用素材卡，无支撑处标 [待补：xxx]",
        needs_selection=False, inject_materials=True, inject_neighbors=True, needs_requirement=True,
    ),
    "polish": ActionSpec(
        label="整章润色",
        task="对整章正文执行润色：保持章节结构与层级、全部技术要点、参数值和表格不变，按用户给定的润色目标提升表达质量；无明确目标时全面提升专业性、具体度与说服力",
        constraint="不得删除任何事实与参数，不得改变章节结构；新增公司事实只准引用素材卡，无支撑处标 [待补：xxx]",
        needs_selection=False, chapter_input=True,
        inject_scoring=True, inject_materials=True,
        ratio=(0.6, 1.6),
        max_tokens=32768, timeout_seconds=900,
    ),
}

SYSTEM_PROMPT_TEMPLATE = """你是技术标书编辑专家。任务：对技术标书中的一个段落执行「{label}」——{task}。

硬约束（违反任何一条即废稿）：
1. 公司事实（业绩、人员、资质、专利、数字）只准引用提供的素材卡原文，不得编造；素材不足处显式标 [待补：具体缺什么]。
2. 技术响应值必须不低于招标要求（招标要求 X，我方响应 ≥X 或优于 X）。
3. 技术卷禁止出现任何报价、价格信息。
4. 只输出处理后的这一段 markdown 正文：不输出解释、不写序言或结语、不用代码块包裹；保持选中内容原有的结构层级（小节标题、列表、表格）。
5. 沿用前后文的术语与语体，不与前后文的事实冲突；不改写、不复述未选中的部分。
6. {constraint}

用户指令（如有）优先级最高，但不得违反以上硬约束。"""

# 空输出纠错后缀（模仿 chat_structured 的喂错语义）：流式输出为空时追加到 user_prompt 整体重流一次
EMPTY_RETRY_SUFFIX = "\n\n你上次输出为空，请重新只输出处理后的这一段完整 markdown 正文。"


def _load_scoring(db: Session, project_id: int, chapter: Chapter, req: CopilotRequest, spec: ActionSpec) -> list[ScoringItemRow]:
    chapter_keys = [k for k in chapter.scoring_keys.split(",") if k]
    # 显式指定评分点的动作：align_scoring 必填；polish 可选（工作台章节无挂接时手动指定）
    explicit = req.action in ("align_scoring", "polish")
    if explicit and req.scoring_key:
        keys = [req.scoring_key]
    elif req.action == "align_scoring":
        keys = chapter_keys
        if not keys:
            raise ValueError("本章未挂接评分点，请在面板中指定要对齐的评分点")
    elif spec.inject_scoring:
        keys = chapter_keys
    else:
        return []
    if not keys:
        return []
    items = (
        db.query(ScoringItemRow)
        .filter(ScoringItemRow.project_id == project_id, ScoringItemRow.item_key.in_(keys))
        .all()
    )
    if explicit and req.scoring_key and not items:
        raise ValueError(f"评分点 {req.scoring_key} 不存在")
    return items


def _snapshot_materials(db: Session, project_id: int, materials: list[Material]) -> None:
    """Q5 引用快照：同项目同卡只存一次。"""
    for m in materials:
        exists = (
            db.query(MaterialSnapshot)
            .filter(MaterialSnapshot.project_id == project_id, MaterialSnapshot.material_id == m.id)
            .first()
        )
        if not exists:
            db.add(MaterialSnapshot(
                project_id=project_id, material_id=m.id,
                content={"type": m.type, "name": m.name, "summary": m.summary,
                         "qual_extra": m.qual_extra, "tags": m.tags, "source": m.source},
            ))


def _soft_check(spec: ActionSpec, selection: str, content: str) -> list[str]:
    warnings = []
    if spec.ratio and selection:
        ratio = len(content) / max(len(selection), 1)
        lo, hi = spec.ratio
        if ratio < lo or ratio > hi:
            warnings.append(f"长度偏离预期（输出/原文 = {ratio:.1f}，预期 {lo}-{hi}）")
    if selection and content.strip() == selection.strip():
        warnings.append("输出与原文相同，模型未做修改")
    if PRICE_PATTERN.search(content):
        warnings.append("疑似含报价信息，技术卷禁止出现价格")
    if "[待补" in content:
        warnings.append("含 [待补] 标记，需补充素材后人工填写")
    return warnings


@dataclass(frozen=True)
class CopilotContext:
    """prepare 阶段产物：流式与非流式共享同一份上下文配方与系统提示词。"""

    spec: ActionSpec  # 动作规格（含第 6 条硬约束）
    system_prompt: str  # SYSTEM_PROMPT_TEMPLATE.format(...)，六条硬约束所在
    user_prompt: str  # 上下文 parts 拼接（章节信息/选区/前后文/评分点/技术要求/素材卡/指令）
    context_refs: dict  # {"scoring_keys": [], "material_ids": [], "requirement_key": ""}
    input_md: str  # 落库 input：selection 或技术要求原文


def prepare_copilot(
    db: Session, project_id: int, chapter: Chapter, req: CopilotRequest
) -> CopilotContext:
    """第一段：组装上下文。契约类错误抛 ValueError（API 层转 422，流式路径在开流前同步调用）。

    素材快照在返回前即提交（幂等）：流式生成期间不持有写锁；被取消只是多存引用快照，无副作用。
    """
    spec = ACTIONS.get(req.action)
    if spec is None:
        raise ValueError(f"未知动作 {req.action}，可选：{'/'.join(ACTIONS)}")
    selection = req.selection_md.strip()
    if spec.chapter_input:
        # 工作台整章动作：输入 = 最新版本全文（服务端自取，客户端不回传大文本）
        v = (
            db.query(ChapterVersion)
            .filter(ChapterVersion.chapter_id == chapter.id)
            .order_by(ChapterVersion.version_no.desc())
            .first()
        )
        selection = (v.content_md if v else "").strip()
        if not selection:
            raise ValueError("本章暂无正文，无法整章润色")
    if spec.needs_selection and not selection:
        raise ValueError("请先选中要处理的段落")

    context_refs: dict = {"scoring_keys": [], "material_ids": [], "requirement_key": ""}
    parts = [f"# 章节信息\n编号：{chapter.chapter_key}\n标题：{chapter.title}"]

    if selection:
        obj_title = "整章正文（处理对象）" if spec.chapter_input else "选中段落（处理对象）"
        parts.append(f"# {obj_title}\n{selection}")

    if spec.inject_neighbors:
        if req.prev_md.strip():
            parts.append(f"# 前文（仅供衔接参考，禁止改写）\n{req.prev_md.strip()[-NEIGHBOR_LIMIT:]}")
        if req.next_md.strip():
            parts.append(f"# 后文（仅供衔接参考，禁止改写）\n{req.next_md.strip()[:NEIGHBOR_LIMIT]}")

    scoring_items = _load_scoring(db, project_id, chapter, req, spec)
    if scoring_items:
        title = "须对齐的评分标准原文" if req.action == "align_scoring" else "本章须响应的评分标准原文（供参考，勿逐字复述）"
        parts.append(f"# {title}")
        for it in scoring_items:
            parts.append(f"## {it.item_key} {it.item}（{it.score} 分）\n{it.criteria_original}")
        context_refs["scoring_keys"] = [it.item_key for it in scoring_items]

    requirement: TechRequirementRow | None = None
    if spec.needs_requirement:
        if not req.requirement_key:
            raise ValueError("请选择要响应的技术要求")
        requirement = (
            db.query(TechRequirementRow)
            .filter(TechRequirementRow.project_id == project_id, TechRequirementRow.req_key == req.requirement_key)
            .first()
        )
        if requirement is None:
            raise ValueError(f"技术要求 {req.requirement_key} 不存在")
        mark = "★" if requirement.star else ""
        parts.append(f"# 须响应的技术要求原文\n[{requirement.req_key}] {mark}{requirement.requirement_original}")
        context_refs["requirement_key"] = requirement.req_key

    if spec.inject_materials:
        # 短词做精确命中，长文本触发 search_materials 的语义类型推断（人员/业绩/研发章节必带对应卡）
        kws = [chapter.title] + [it.item for it in scoring_items]
        if selection:
            kws.append(selection[:200])
        if requirement is not None:
            kws.append(requirement.requirement_original[:100])
        materials = search_materials(db, kws, limit=MATERIAL_LIMIT)
        if materials:
            parts.append("# 公司素材卡（公司事实只准引用以下内容；卡片标 [待补] 的字段在正文对应处照标 [待补]）")
            for m in materials:
                parts.append(f"## 素材卡 [{m.type}] {m.name}\n{m.summary}")
                if m.qual_extra:
                    parts.append("资格字段：" + json.dumps(m.qual_extra, ensure_ascii=False))
            _snapshot_materials(db, project_id, materials)
            context_refs["material_ids"] = [m.id for m in materials]
        else:
            parts.append("# 素材库\n（未检索到相关素材卡，涉及公司业绩/人员/资质处一律标 [待补：xxx]）")

    if req.instruction.strip():
        parts.append(f"# 用户指令\n{req.instruction.strip()}")

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(label=spec.label, task=spec.task, constraint=spec.constraint)
    # 提前提交素材快照（幂等）：避免流式生成期间持有写锁阻塞其他请求（database is locked 教训）
    db.commit()
    return CopilotContext(
        spec=spec,
        system_prompt=system_prompt,
        user_prompt="\n\n".join(parts),
        context_refs=context_refs,
        input_md=selection or (requirement.requirement_original if requirement else ""),
    )


def finalize_copilot(
    db: Session, project_id: int, chapter: Chapter, req: CopilotRequest,
    user_id: int | None, ctx: CopilotContext, content: str, latency_ms: int,
) -> CopilotResponse:
    """第三段：软校验 + 落 copilot_actions。content 须为已 strip 的全文，latency_ms 由调用方计时。"""
    selection = req.selection_md.strip()
    warnings = _soft_check(ctx.spec, selection, content)

    row = CopilotAction(
        project_id=project_id, chapter_id=chapter.id, user_id=user_id,
        action=req.action, instruction=req.instruction.strip()[:500],
        scoring_key=",".join(ctx.context_refs["scoring_keys"])[:32] if req.action == "align_scoring" else "",
        input_md=ctx.input_md,
        output_md=content, warnings=";".join(warnings)[:500],
        latency_ms=latency_ms, model=resolve_llm("copilot").model,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return CopilotResponse(request_id=row.id, content_md=content, warnings=warnings, context_refs=ctx.context_refs)


def run_copilot(
    db: Session, project_id: int, chapter: Chapter, req: CopilotRequest, user_id: int | None
) -> CopilotResponse:
    """单次动作（非流式）：契约类错误抛 ValueError（API 层转 422），LLM 失败抛 LLMError（转 502）。"""
    ctx = prepare_copilot(db, project_id, chapter, req)
    t0 = time.monotonic()
    result = chat_structured(
        ctx.system_prompt, ctx.user_prompt, CopilotResult, layer="copilot",
        max_tokens=ctx.spec.max_tokens, timeout=ctx.spec.timeout_seconds,
    )
    latency_ms = int((time.monotonic() - t0) * 1000)
    return finalize_copilot(db, project_id, chapter, req, user_id, ctx, result.content_md.strip(), latency_ms)
