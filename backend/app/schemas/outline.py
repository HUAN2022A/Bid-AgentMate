"""大纲章节树的 LLM 输出契约（Q27）。

树形递归结构：nodes 为一级章，children 递归。scoring_keys 挂评分点（S1/S2…）。
"""
from pydantic import BaseModel, Field


class OutlineNode(BaseModel):
    id: str = Field(description="章节层级编号，如 1、1.1、1.1.1，稳定不重复")
    title: str = Field(description="章节标题")
    target_words: int = Field(default=2000, description="目标字数（按挂接评分点分值加权分配）")
    scoring_keys: list[str] = Field(default_factory=list, description="挂接的技术类评分点 id（S1/S2…）")
    children: list["OutlineNode"] = Field(default_factory=list)


class OutlineTree(BaseModel):
    nodes: list[OutlineNode]


OutlineNode.model_rebuild()
