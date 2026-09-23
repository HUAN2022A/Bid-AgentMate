"""章节内 Copilot 的请求与 LLM 输出契约（Q26：Pydantic 模型即契约）。"""
from pydantic import BaseModel, Field


class CopilotRequest(BaseModel):
    action: str = Field(description="rewrite | expand | compress | align_scoring | tabulate | star_response")
    selection_md: str = Field(default="", description="选中段落 markdown；star_response 可为空")
    instruction: str = Field(default="", max_length=500, description="用户补充指令，优先级最高但不得违反硬约束")
    scoring_key: str = Field(default="", description="align_scoring 指定评分点 id；缺省取本章挂接的评分点")
    requirement_key: str = Field(default="", description="star_response 指定技术需求 id")
    prev_md: str = Field(default="", description="选中段前一块正文（衔接参考）")
    next_md: str = Field(default="", description="选中段后一块正文（衔接参考）")


class CopilotResult(BaseModel):
    """LLM 输出：只有处理后的整段。min_length 让空输出走校验失败→喂错重试。"""

    content_md: str = Field(min_length=1, description="处理后的完整段落 markdown，不含任何解释、标题或代码块包裹")


class CopilotResponse(BaseModel):
    request_id: int
    content_md: str
    warnings: list[str] = []
    context_refs: dict = {}
