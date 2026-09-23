"""投标文件目录树契约（工作台）：与起草大纲 OutlineTree 分开，叶节点携带导入正文。

树存在 outline_drafts/outline_snapshots（doc_kind='bid'）里；确认时物化为 chapters +
chapter_versions(source='imported')。非叶节点的自有正文（章引导段）并入首个叶章内容开头。
"""
from pydantic import BaseModel, Field


class BidOutlineNode(BaseModel):
    id: str = Field(description="章节层级编号，如 1、1.1，按树位置稳定编号")
    title: str = Field(description="章节标题（导入自投标文件目录，可编辑）")
    content_md: str = Field(default="", description="叶章正文；非叶章确认时并入首个叶章")
    children: list["BidOutlineNode"] = Field(default_factory=list)


class BidOutlineTree(BaseModel):
    nodes: list[BidOutlineNode]


BidOutlineNode.model_rebuild()
