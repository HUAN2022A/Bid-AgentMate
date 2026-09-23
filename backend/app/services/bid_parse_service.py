"""投标文件解析（工作台）：提取 → 章节树 → 草稿落库 + 确认物化。

章节树双路（金标准实测：真实投标 docx 带 200 个真实标题层级，标题路径为主）：
- 标题路径：docx Heading/标题样式 + outlineLvl → 按层级栈切树，零 LLM、确定性；
- LLM 兜底：pdf 或无样式 docx → 分块让 LLM 标出标题行（anchor 逐字回对原文行），再走同一栈算法。

层级跳跃规整：## 之后直接 #### → 视为 ###（真实文件里常见）。
非叶章自有引导段并入首个叶章内容开头；首标题前的前言同样并入首个叶章。
"""
import json
import re
import sys
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from app.core.config import settings  # noqa: E402
from app.core.llm import chat_structured  # noqa: E402
from app.core.storage import storage  # noqa: E402
from app.models.chapter import Chapter, ChapterVersion  # noqa: E402
from app.models.file_object import FileObject  # noqa: E402
from app.models.outline import OutlineDraft  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.models.tender_file import TenderFile  # noqa: E402
from app.schemas.bid_outline import BidOutlineTree  # noqa: E402
from app.services.doc_converter import convert_doc_to_docx  # noqa: E402

from scripts.extract_docx_structured import extract_docx_structured  # noqa: E402
from scripts.extract_pdf import extract_pdf_lines  # noqa: E402

_HEADING_RE = re.compile(r"^(#{1,9})\s+(.+)$")

# 标题路径的最低置信：少于 3 个标题视同无结构，走 LLM 兜底
_MIN_HEADINGS = 3

# LLM 兜底分块预算（字符）
_LLM_CHUNK_BUDGET = 40_000

HEADING_SYSTEM_PROMPT = """你是标书文档结构分析专家。任务：从投标文件文本片段中找出全部章节标题行。

规则：
1. 只输出真正的章节标题（章/节/条目标题、带编号的标题行），正文句子不算。
2. level 为标题层级 1-5：文档最大结构（如"一、投标函"）为 1，其下小节为 2，依此类推；拿不准时宁浅勿深。
3. anchor 必须是该标题在原文中所在行的逐字原文（含序号与标点），用于回对位置；禁止改写。
4. 没有标题时输出空数组。"""


class BidHeadingHit(BaseModel):
    title: str = Field(description="章节标题")
    level: int = Field(ge=1, le=5, description="标题层级 1-5")
    anchor: str = Field(description="该标题所在原文行的逐字原文")


class BidHeadingHits(BaseModel):
    headings: list[BidHeadingHit]


# ---------------------------------------------------------------------------
# 章节树构建（标题路径）
# ---------------------------------------------------------------------------

def _build_nodes(lines: list[str]) -> list[dict]:
    """层级栈切树。返回 [{title, children[], lines[]}]（lines 仅自身正文）。"""
    roots: list[dict] = []
    stack: list[dict] = []  # stack[i] = 深度 i+1 的当前节点
    preamble: list[str] = []
    cur: dict | None = None
    for ln in lines:
        m = _HEADING_RE.match(ln)
        if m:
            lvl = min(len(m.group(1)), len(stack) + 1)  # 规整层级跳跃
            title = m.group(2).strip()
            if not title:
                (cur["lines"] if cur else preamble).append(ln)
                continue
            node: dict = {"title": title, "children": [], "lines": []}
            del stack[lvl - 1:]
            (stack[-1]["children"] if stack else roots).append(node)
            stack.append(node)
            cur = node
        else:
            (cur["lines"] if cur else preamble).append(ln)
    if preamble:  # 首标题前的封面/前言 → 并入第一个叶章（无章节时留作单章正文）
        if roots:
            _first_leaf(roots)["lines"] = preamble + _first_leaf(roots)["lines"]
        else:
            roots = [{"title": "正文", "children": [], "lines": preamble}]
    return roots


def _first_leaf(roots: list[dict]) -> dict:
    node = roots[0]
    while node["children"]:
        node = node["children"][0]
    return node


def _serialize(nodes: list[dict], prefix: str = "", intro: list[str] | None = None) -> list[dict]:
    """内部节点树 → BidOutlineNode dict 列表（层级编号 + 叶章正文）。

    intro 折叠：父章自有引导段（含更上层下沉的）只并入首个子树的叶章，其余兄弟章不沾。
    """
    out: list[dict] = []
    for i, n in enumerate(nodes, 1):
        node_id = f"{i}" if not prefix else f"{prefix}.{i}"
        own = [l for l in n["lines"] if l.strip()]
        carry = (list(intro or []) + own) if i == 1 else own
        if n["children"]:
            out.append({
                "id": node_id,
                "title": n["title"],
                "content_md": "",
                "children": _serialize(n["children"], node_id, carry),
            })
        else:
            out.append({
                "id": node_id,
                "title": n["title"],
                "content_md": "\n".join(carry),
                "children": [],
            })
    return out


def build_tree_from_heading_lines(lines: list[str]) -> dict:
    roots = _build_nodes(lines)
    return {"nodes": _serialize(roots)}


# ---------------------------------------------------------------------------
# LLM 兜底路径
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    return re.sub(r"[\s　:：、，。;；\-—_]+", "", s)


def _match_anchor(anchor: str, chunk: list[str], start: int) -> int | None:
    """anchor 回对 chunk 内的行号（先归一化全等，再子串），返回原始行号或 None。"""
    na = _normalize(anchor)
    if not na:
        return None
    for i in range(start, len(chunk)):
        if _normalize(chunk[i]) == na:
            return i
    for i in range(start, len(chunk)):
        if na in _normalize(chunk[i]) or _normalize(chunk[i]) in na:
            return i
    return None


def _llm_mark_headings(lines: list[str]) -> list[str]:
    """分块让 LLM 标标题行，返回标题行带 # 前缀的行表（未命中 anchor 的标题丢弃）。"""
    marked = list(lines)
    dropped = 0
    for start in range(0, len(lines), 200):  # 按 200 行步进聚块到字符预算
        chunk_idx = list(range(start, min(start + 200, len(lines))))
        while chunk_idx and sum(len(lines[i]) for i in chunk_idx) < _LLM_CHUNK_BUDGET and chunk_idx[-1] + 1 < len(lines):
            chunk_idx.append(chunk_idx[-1] + 1)
        chunk = [lines[i] for i in chunk_idx]
        if not any(c.strip() for c in chunk):
            continue
        if settings.sample_mode:
            continue  # 样例模式无 LLM：不标标题（迷你样例走标题路径）
        text = "\n".join(chunk)
        hits = chat_structured(
            HEADING_SYSTEM_PROMPT,
            f"投标文件文本片段如下：\n\n{text}",
            BidHeadingHits,
            layer="parse",
        )
        search_from = 0
        for h in hits.headings:
            i = _match_anchor(h.anchor, chunk, search_from)
            if i is None:
                dropped += 1
                continue
            marked[chunk_idx[i]] = "#" * h.level + " " + h.title
            search_from = i + 1
    if dropped:
        # 不阻断：丢锚的标题无法定位正文，宁缺毋滥
        print(f"[bid_parse] LLM 标题 {dropped} 个未匹配到原文行，已丢弃", file=sys.stderr)
    return marked


# ---------------------------------------------------------------------------
# 提取 + 编排
# ---------------------------------------------------------------------------

def extract_bid_lines(db: Session, project: Project) -> tuple[list[str], dict]:
    """取最新一份投标文件提取结构化行（.doc 先转 .docx，幂等）。更新 TenderFile 统计。"""
    tender = (
        db.query(TenderFile)
        .filter(TenderFile.project_id == project.id, TenderFile.role == "bid")
        .order_by(TenderFile.id.desc())
        .first()
    )
    if tender is None:
        raise ValueError("未找到投标文件（role=bid）")
    fo = db.get(FileObject, tender.file_object_id)
    src = storage.abspath(fo.relative_path)

    if tender.file_type == "doc":
        conv_rel = f"projects/{project.id}/bid/converted-{tender.id}.docx"
        if not storage.exists(conv_rel):
            convert_doc_to_docx(str(src), str(storage.abspath(conv_rel)))
        src = storage.abspath(conv_rel)
        lines, stats = extract_docx_structured(str(src))
    elif tender.file_type == "docx":
        lines, stats = extract_docx_structured(str(src))
    else:  # pdf：无样式信息，行原样（无 # 标记 → 自然走 LLM 兜底）
        lines, stats = extract_pdf_lines(str(src))

    text_rel = f"projects/{project.id}/bid/extracted-{tender.id}.txt"
    storage.put(text_rel, "\n".join(lines).encode("utf-8"))
    tender.extracted_text_path = text_rel
    tender.extract_stats = json.dumps(stats, ensure_ascii=False)
    db.flush()
    return lines, stats


def run_bid_parse(db: Session, project: Project) -> None:
    """投标文件 → 章节树草稿（OutlineDraft doc_kind='bid'）。失败向上抛，状态由编排方处理。"""
    lines, stats = extract_bid_lines(db, project)
    db.commit()  # 释放提取阶段的写锁（LLM 兜底路径的调用耗时较长）

    if stats.get("headings", 0) >= _MIN_HEADINGS:
        tree = build_tree_from_heading_lines(lines)
    else:
        tree = build_tree_from_heading_lines(_llm_mark_headings(lines))

    BidOutlineTree.model_validate(tree)  # 契约校验，非法即抛
    draft = (
        db.query(OutlineDraft)
        .filter(OutlineDraft.project_id == project.id, OutlineDraft.doc_kind == "bid")
        .first()
    )
    if draft is None:
        draft = OutlineDraft(project_id=project.id, doc_kind="bid", tree=tree, ai_raw_tree=tree)
        db.add(draft)
    else:
        draft.tree = tree
        draft.ai_raw_tree = tree
    db.flush()


# ---------------------------------------------------------------------------
# 确认物化
# ---------------------------------------------------------------------------

def _serialized_leaves(nodes: list[dict], intro: str = ""):
    """先序遍历序列化树，yield (叶节点, 含父章引导段的正文)。intro 只随首个子树下沉。"""
    first = True
    for n in nodes:
        own = (n.get("content_md") or "").strip()
        carry = f"{intro}\n{own}".strip() if (intro and own) else (intro or own)
        children = n.get("children") or []
        if children:
            yield from _serialized_leaves(children, carry if first else own)
        else:
            yield n, carry
        first = False


def materialize_bid_chapters(db: Session, project: Project, tree: dict) -> int:
    """目录树 → chapters + chapter_versions(source='imported')。重建式（重复确认覆盖）。"""
    ch_ids = [c.id for c in db.query(Chapter).filter(Chapter.project_id == project.id).all()]
    if ch_ids:
        db.query(ChapterVersion).filter(ChapterVersion.chapter_id.in_(ch_ids)).delete()
        db.query(Chapter).filter(Chapter.project_id == project.id).delete()
    db.flush()

    order = 0
    for node, content in _serialized_leaves(tree.get("nodes", [])):
        order += 1
        ch = Chapter(
            project_id=project.id,
            chapter_key=node["id"],
            title=node["title"],
            target_words=max(len(content), 1),  # 导入章节目标=现状字数（驾驶舱缺口口径不失真）
            sort_order=order,
            state="imported",
            outline_version=project.bid_outline_version,
        )
        db.add(ch)
        db.flush()
        db.add(ChapterVersion(
            chapter_id=ch.id,
            version_no=1,
            content_md=content,
            word_count=len(content),
            source="imported",
        ))
    db.flush()
    return order
