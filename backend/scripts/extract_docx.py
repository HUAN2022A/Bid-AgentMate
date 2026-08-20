#!/usr/bin/env python3
"""提取 docx 全文（段落 + 表格按文档顺序），输出带标记的纯文本。"""
import argparse
import sys
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph


def iter_blocks(doc):
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield Table(child, doc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="docx 路径")
    ap.add_argument("-o", "--output", required=True, help="输出 txt 路径")
    args = ap.parse_args()

    doc = Document(args.input)
    lines = []
    for block in iter_blocks(doc):
        if isinstance(block, Paragraph):
            t = block.text.strip()
            if t:
                style = block.style.name if block.style else ""
                prefix = "# " if ("Heading" in style or "标题" in style) else ""
                lines.append(prefix + t)
        else:
            lines.append("[表格]")
            for row in block.rows:
                cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                lines.append(" | ".join(cells))
            lines.append("[/表格]")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    total = sum(len(l) for l in lines)
    print(f"行数: {len(lines)}, 总字符: {total} → {out}")
    if len(lines) > 0 and total / len(lines) < 20:
        print("WARN: 平均每行字符偏少，可能含扫描图片内容，注意核对", file=sys.stderr)


if __name__ == "__main__":
    main()
