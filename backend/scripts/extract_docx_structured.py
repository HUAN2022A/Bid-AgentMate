#!/usr/bin/env python3
"""按文档结构提取 docx：保留标题层级（投标文件章节树的主来源）。

与 extract_docx_lines 的差异：标题行带层级前缀（## 二级），供 bid_parse_service 切树。
层级来源优先级：w:outlineLvl 显式大纲级 > 样式名（Heading N / 标题 N）。
"""
import re

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

_STYLE_LEVEL_RE = re.compile(r"^(?:Heading|标题)\s*(\d)$", re.IGNORECASE)


def _heading_level(par: Paragraph) -> int | None:
    """返回标题层级 1-9，非标题返回 None。"""
    # 1) 段落级显式大纲级别（w:outlineLvl val=0 → 一级）
    pPr = par._p.pPr  # noqa: SLF001
    if pPr is not None and pPr.outlineLvl is not None:
        try:
            lvl = int(pPr.outlineLvl.val) + 1
            if 1 <= lvl <= 9:
                return lvl
        except (TypeError, ValueError):
            pass
    # 2) 样式名
    name = par.style.name if par.style else ""
    m = _STYLE_LEVEL_RE.match(name.strip())
    if m:
        return min(int(m.group(1)), 9)
    return None


def extract_docx_structured(input_path: str) -> tuple[list[str], dict]:
    """库函数入口：返回 (行列表, 统计)。标题行 = '#'*level + ' ' + 文本，其余行原样。

    行格式与 extract_docx_lines 兼容（表格 [表格]/| a | b |/[/表格]），仅标题前缀带层级。
    """
    doc = Document(input_path)
    lines: list[str] = []
    headings = 0
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            par = Paragraph(child, doc)
            t = par.text.strip()
            if not t:
                continue
            lvl = _heading_level(par)
            if lvl:
                headings += 1
                lines.append("#" * lvl + " " + t)
            else:
                lines.append(t)
        elif child.tag == qn("w:tbl"):
            table = Table(child, doc)
            lines.append("[表格]")
            for row in table.rows:
                cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                lines.append(" | ".join(cells))
            lines.append("[/表格]")

    total = sum(len(l) for l in lines)
    stats = {
        "lines": len(lines),
        "chars": total,
        "tables": sum(1 for l in lines if l == "[表格]"),
        "headings": headings,
        "maybe_scanned": bool(lines and total / len(lines) < 20),
    }
    return lines, stats


if __name__ == "__main__":
    import argparse
    import sys
    from pathlib import Path

    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="docx 路径")
    ap.add_argument("-o", "--output", required=True, help="输出 txt 路径")
    args = ap.parse_args()

    ls, st = extract_docx_structured(args.input)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(ls), encoding="utf-8")
    print(f"行数: {st['lines']}, 字符: {st['chars']}, 标题: {st['headings']}, 表格: {st['tables']} → {out}")
    if st["maybe_scanned"]:
        print("WARN: 平均每行字符偏少，可能含扫描图片内容，注意核对", file=sys.stderr)
