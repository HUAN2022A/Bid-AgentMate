"""招标文件解析的 LLM 输出契约（Q26：Pydantic 模型即契约）。

字段与 bid-parse 的 tender-analysis.yaml schema 对齐——软件化不改契约，
下游（大纲生成/起草/自查）消费方式与 skill 版一致。
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ScoringItem(BaseModel):
    id: str = Field(description="评分项编号，如 S1")
    category: Literal["技术", "商务", "价格", "资质", "其他"] = Field(description="分卷标签")
    item: str = Field(description="评分项名称")
    score: float = Field(description="分值")
    criteria_original: str = Field(description="评分标准原文，逐字照抄，禁止改写")
    location: str = Field(default="", description="章节定位 + 页码")
    response_hint: str = Field(default="", description="仅技术项：一句话说明在技术卷哪里、用什么响应")
    note: str = Field(default="", description="可选备注")


class Scoring(BaseModel):
    mode: Literal["总分制", "加权制"] = Field(description="总分制：items 分值加总=total；加权制：各卷 100 分制后按 weights 加权")
    total: Optional[float] = Field(default=None, description="总分制时的总分（通常 100）")
    weights: Optional[dict[str, float]] = Field(default=None, description="加权制时的分卷权重，加总须为 1.0")
    items: list[ScoringItem]


class TechRequirement(BaseModel):
    id: str = Field(description="技术需求编号，如 T1")
    star: bool = Field(default=False, description="是否 ★/▲ 号条款（硬指标，不得负偏离）")
    requirement_original: str = Field(description="技术需求原文，逐字照抄")
    location: str = Field(default="")


class DisqualificationClause(BaseModel):
    id: str = Field(description="废标条款编号，如 D1")
    clause_original: str = Field(description="废标/无效投标/否决条款原文，逐字照抄")
    location: str = Field(default="")
    applies_to: str = Field(default="全局", description="技术卷|全局")
    manual: bool = Field(default=False, description="true=商务/资质类，需人工处理")


class FormatRequirement(BaseModel):
    id: str = Field(description="格式要求编号，如 F1")
    requirement_original: str = Field(description="格式要求原文（字体、份数、密封、页码、目录等）")
    location: str = Field(default="")


class StructureRequirement(BaseModel):
    id: str = Field(description="结构要求编号，如 R1")
    requirement_original: str = Field(description="招标文件明确要求技术文件包含的内容，原文")
    location: str = Field(default="")


class QualificationItem(BaseModel):
    id: str = Field(description="资格要求编号，如 Q1")
    requirement_original: str = Field(description="资格/门槛要求原文（业绩、职称、资质、联合体、限价、保证金等）")
    location: str = Field(default="")
    note: str = Field(default="")


class CommercialNote(BaseModel):
    id: str = Field(description="商务要点编号，如 C1")
    requirement_original: str = Field(description="付款/违约/质保等商务条款原文，浅提取")
    location: str = Field(default="")


class TenderAnalysisResult(BaseModel):
    """LLM 解析招标文件的完整输出。只提取，不评价、不给建议。"""

    project_name: str = Field(description="项目名称（从封面/首页提取）")
    tender_no: str = Field(default="", description="招标编号")
    scoring: Scoring
    tech_requirements: list[TechRequirement] = Field(default_factory=list)
    qualification: list[QualificationItem] = Field(default_factory=list)
    commercial_notes: list[CommercialNote] = Field(default_factory=list)
    disqualification: list[DisqualificationClause] = Field(default_factory=list)
    format_requirements: list[FormatRequirement] = Field(default_factory=list)
    structure_requirements: list[StructureRequirement] = Field(default_factory=list)
