#!/usr/bin/env python3
"""pdfplumber 提取 PDF 招标文件全文：文本 + 表格结构还原。

与 extract_docx.py 输出格式完全一致（=== 第 N 页 === 标记、[表格] 块、
单元格以 " | " 分隔），下游关键词定位/章节识别无差别。
相比 pypdf 纯文本提取：表格保留行列结构，评分办法、参数表可直接读。
"""
import argparse
import sys
from pathlib import Path

import pdfplumber


def in_any_bbox(obj, bboxes):
    """对象中心点是否落在任一表格 bbox 内（用于把表格区域从正文文本中剔除）。"""
    cx, cy = (obj["x0"] + obj["x1"]) / 2, (obj["top"] + obj["bottom"]) / 2
    return any(bb[0] <= cx <= bb[2] and bb[1] <= cy <= bb[3] for bb in bboxes)


def table_rows(table):
    rows = []
    for raw in table.extract():
        cells = [("" if c is None else str(c).strip().replace("\n", " ")) for c in raw]
        rows.append(" | ".join(cells))
    return rows


def page_lines_ordered(page, page_no):
    """正文与表格按垂直位置交错的页输出（词级重建正文行）。"""
    out = [f"=== 第 {page_no} 页 ==="]
    tables = page.find_tables()
    bboxes = [t.bbox for t in tables]

    def outside(obj):
        return not in_any_bbox(obj, bboxes)

    items = []  # (top, is_table, payload)

    if tables:
        filtered = page.filter(outside)
        words = filtered.extract_words(use_text_flow=False, keep_blank_chars=False)
    else:
        words = page.extract_words(use_text_flow=False, keep_blank_chars=False)

    # 词 → 行（top 相近归同一行，容差 3pt）
    words.sort(key=lambda w: (round(w["top"] / 3), w["x0"]))
    lines, cur, cur_top = [], [], None
    for w in words:
        if cur_top is None or abs(w["top"] - cur_top) <= 3:
            cur.append(w["text"])
            cur_top = w["top"] if cur_top is None else cur_top
        else:
            lines.append((cur_top, " ".join(cur)))
            cur, cur_top = [w["text"]], w["top"]
    if cur:
        lines.append((cur_top, " ".join(cur)))

    for top, text in lines:
        items.append((top, False, text))
    for t in tables:
        items.append((t.bbox[1], True, table_rows(t)))

    items.sort(key=lambda it: it[0])
    for _, is_table, payload in items:
        if is_table:
            out.append("[表格]")
            out.extend(payload)
            out.append("[/表格]")
        else:
            out.append(payload)
    return out


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="PDF 路径")
    ap.add_argument("-o", "--output", required=True, help="输出 txt 路径")
    args = ap.parse_args()

    lines = []
    n_tables = 0
    with pdfplumber.open(args.input) as pdf:
        total_pages = len(pdf.pages)
        for i, page in enumerate(pdf.pages, 1):
            try:
                page_lines = page_lines_ordered(page, i)
            except Exception as e:  # 单页失败不中断整体提取
                page_lines = [f"=== 第 {i} 页 ===", f"[本页提取失败: {e}]"]
            n_tables += sum(1 for l in page_lines if l == "[表格]")
            lines.extend(page_lines)
            if i % 20 == 0:
                print(f"  ... 已提取 {i}/{total_pages} 页", file=sys.stderr)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")

    total_chars = sum(len(l) for l in lines if not l.startswith("==="))
    print(f"页数: {total_pages}, 行数: {len(lines)}, 表格: {n_tables} 个, 总字符: {total_chars} → {out}")
    if total_pages and total_chars / total_pages < 100:
        print("WARN: 平均每页字符偏少，可能是扫描版 PDF，需要 OCR 后再提取。", file=sys.stderr)


if __name__ == "__main__":
    main()
