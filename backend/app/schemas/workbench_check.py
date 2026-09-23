"""工作台检查的 LLM 输出契约（废标项裁决 / 错别字扫描；逻辑谬误与估分 M4）。

错别字命中的 original 必须是原文的逐字连续片段（服务端回验 in-chunk，未命中即丢弃），
保证每条 finding 的证据可在原文中定位（验收口径）。
"""
from typing import Literal

from pydantic import BaseModel, Field


class TypoHit(BaseModel):
    original: str = Field(description="原文中的错误片段，逐字连续复制（含错字及少量上下文，总长 4-20 字）")
    corrected: str = Field(description="修正后的片段（同一片段仅改正错字，不改写其他内容）")
    reason: str = Field(description="错因说明，如：'安装'误写为'按装'")


class TypoScanResult(BaseModel):
    typos: list[TypoHit] = Field(default_factory=list, description="确定性高的错别字；没有则空数组")


class DisqVerdict(BaseModel):
    clause_id: str = Field(description="废标条款编号，如 D1（原样返回）")
    verdict: Literal["pass", "risk", "needs_review"] = Field(
        description="pass=文本可证满足；risk=存在违反风险；needs_review=无法仅凭文本判断（如盖章/签名/份数）"
    )
    evidence: str = Field(description="投标文件中的证据原文引用（逐字）；无直接证据时留空")
    analysis: str = Field(description="分析说明：为什么得出该结论")
    suggestion: str = Field(description="修改建议或人工复核提示")


class DisqScanResult(BaseModel):
    verdicts: list[DisqVerdict]


class ScoreEstimateItem(BaseModel):
    """单个技术评分点的模拟估分。"""

    item_key: str = Field(description="评分点编号，如 S1（原样返回）")
    estimated_score: float = Field(ge=0, description="预估得分（0 到最高分之间；响应充分程度按评分标准逐要素估）")
    evidence: str = Field(description="投标文件中的支撑证据原文引用（逐字，可多条用换行分隔；无证据时留空）")
    deduction_reasons: str = Field(description="扣分理由：未响应/响应不充分的评分要素，逐条说明")
    improvement_advice: str = Field(description="改进建议：补什么内容、在哪个章节补，可直接执行")


class ScoreEstimateResult(BaseModel):
    estimates: list[ScoreEstimateItem]


class LogicIssue(BaseModel):
    quote: str = Field(description="存在逻辑问题的原文片段，逐字连续复制（10-60 字）")
    issue_type: str = Field(description="问题类型，如：前后矛盾/以偏概全/因果倒置/承诺超出能力/数据不一致")
    analysis: str = Field(description="逻辑问题分析：与何处矛盾、为什么不成立")
    suggestion: str = Field(description="修改建议")


class LogicScanResult(BaseModel):
    issues: list[LogicIssue] = Field(default_factory=list, description="确定性较高的逻辑问题；没有则空数组")
