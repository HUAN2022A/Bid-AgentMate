"""逐章起草的 LLM 输出契约。"""
from pydantic import BaseModel, Field


class ChapterDraft(BaseModel):
    content_md: str = Field(description="章节正文 markdown。硬约束：公司事实只引用提供的素材卡原文；缺素材处显式标 [待补：xxx]；技术响应值不低于招标要求；技术卷禁止出现报价信息")
    covers: list[str] = Field(default_factory=list, description="本章覆盖的评分点 id 列表（S4/S5…）")
    pending_gaps: list[str] = Field(default_factory=list, description="本章 [待补] 清单摘要")
