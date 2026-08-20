#!/usr/bin/env python3
"""bid-check 机器可判定部分：评分点覆盖、技术需求关键词、章节齐全、待补扫描、报价混入检测。

输出结构化文本供汇总进 check-report.md。需要判断的检查（响应质量）由模型补充。

--fix 模式：自动修复低风险项（covers 注释、文件名 id、图注编号、图片路径），
只碰元数据与编号，不改正文语义；修复前自动备份 chapters/ 到 chapters.bak-<时间戳>/。
"""
import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("缺少 PyYAML，请先: pip install pyyaml")


def flatten(chapters, out=None):
    out = out if out is not None else []
    for ch in chapters or []:
        out.append(ch)
        flatten(ch.get("children"), out)
    return out


def read_chapters(chdir):
    """返回 {文件名: 信息}，解析 covers 注释（保留原文供 --fix 重写）。"""
    res = {}
    for f in sorted(Path(chdir).glob("*.md")):
        text = f.read_text(encoding="utf-8")
        m = re.search(r"<!--\s*covers:(.*?)-->", text, re.S)
        covers_raw = m.group(0) if m else ""
        covers = m.group(1) if m else ""
        scoring = re.findall(r"scoring=\[([^\]]*)\]", covers)
        tech = re.findall(r"tech_reqs=\[([^\]]*)\]", covers)
        res[f.name] = {
            "text": text,
            "covers_raw": covers_raw,
            "scoring": [s.strip() for s in scoring[0].split(",")] if scoring and scoring[0].strip() else [],
            "tech_reqs": [s.strip() for s in tech[0].split(",")] if tech and tech[0].strip() else [],
            "pending": len(re.findall(r"\[待补", text)),
            "words": len(re.sub(r"<!--.*?-->", "", text, flags=re.S).replace("\n", "").replace(" ", "")),
        }
    return res


def chapter_sort_key(name):
    m = re.match(r"^([\d.]+)", name)
    if not m:
        return (999,)
    return tuple(int(x) for x in m.group(1).rstrip(".").split("."))


# ── 可自动修复项：checker/fixer 配对 ──

def check_covers(chapters, o_chapters, ws, fix):
    """covers 注释完整性：scoring 须与 outline 一致（子节继承父章挂点）；
    tech_reqs 允许是父章全集的子集（起草时按节认领），只补缺失的 scoring。
    fix 时重写文件头注释。"""
    issues, applied = [], []
    by_id = {str(c.get("id")): c for c in o_chapters}
    for fname, info in chapters.items():
        cid = re.match(r"^([\d.]+)", fname)
        cid = cid.group(1).rstrip(".") if cid else ""
        # 子节继承父章挂点（outline 通常把 scoring/tech_reqs 挂在一级章）
        exp_s, exp_t = [], []
        parts = cid.split(".")
        for i in range(len(parts), 0, -1):
            node = by_id.get(".".join(parts[:i]))
            if node:
                exp_s = [str(s) for s in (node.get("scoring") or [])]
                exp_t = [str(t) for t in (node.get("tech_reqs") or [])]
                if exp_s or exp_t:
                    break
        # scoring 必须精确一致；tech_reqs 是认领制，只报"声明了父章没有的"（越界）
        scoring_ok = sorted(info["scoring"]) == sorted(exp_s)
        overclaimed = [t for t in info["tech_reqs"] if t not in exp_t]
        if scoring_ok and not overclaimed:
            continue
        detail = []
        if not scoring_ok:
            detail.append(f"scoring 声明={info['scoring']} 应为={exp_s}")
        if overclaimed:
            detail.append(f"tech_reqs 越界声明={overclaimed}（父章挂点={exp_t}）")
        issues.append(f"{fname}: {'；'.join(detail)}")
        if fix:
            path = ws / "chapters" / fname
            text = path.read_text(encoding="utf-8")
            pending = len(re.findall(r"\[待补", text))
            keep_t = [t for t in info["tech_reqs"] if t in exp_t]
            new_comment = (f"<!-- covers: scoring=[{','.join(exp_s)}] "
                           f"tech_reqs=[{','.join(keep_t)}] pending={pending} -->")
            if info["covers_raw"]:
                text = text.replace(info["covers_raw"], new_comment, 1)
            else:
                text = new_comment + "\n" + text
            path.write_text(text, encoding="utf-8")
            applied.append(f"{fname}: covers 重写为 scoring={exp_s} tech_reqs={keep_t}")
    return issues, applied


def check_filename_id(chapters, o_chapters, ws, fix):
    """文件名前缀 id 与 outline 章节 id 一致性。fix 时按 outline id 重命名文件。"""
    issues, applied = [], []
    valid_ids = {str(c.get("id")) for c in o_chapters}
    for fname in chapters:
        m = re.match(r"^([\d.]+)(-.*)?\.md$", fname)
        if not m:
            issues.append(f"{fname}: 文件名无章节 id 前缀")
            continue
        fid = m.group(1).rstrip(".")
        if fid in valid_ids:
            continue
        # 找最接近的合法 id（相同数字序列不同分隔，如 2.10 vs 2.1.0 不做猜，只处理完全找不到的）
        issues.append(f"{fname}: id 前缀 '{fid}' 不在大纲章节 id 中")
        if fix:
            # 仅当前缀是大纲 id 的明显笔误（数字相同仅点多寡不同）才自动改
            norm = fid.replace(".", "")
            cand = [v for v in valid_ids if v.replace(".", "") == norm]
            if len(cand) == 1:
                newname = cand[0] + (m.group(2) or "") + ".md"
                (ws / "chapters" / fname).rename(ws / "chapters" / newname)
                applied.append(f"{fname} → {newname}")
            else:
                applied.append(f"{fname}: 无法唯一推断正确 id，未改名（需人工）")
    return issues, applied


def _figure_scan(chapters):
    """扫描全文图注与正文引用，返回 {文件名: [(行号, 完整match, 章号, 序号, 标题, 路径)]}。"""
    figs = {}
    for fname, info in chapters.items():
        items = []
        for ln, line in enumerate(info["text"].split("\n")):
            m = re.match(r"^!\[图\s*(\d+)-(\d+)\s*([^\]]*)\]\(([^)]+)\)", line.strip())
            if m:
                items.append((ln, m.group(0), m.group(1), int(m.group(2)), m.group(3).strip(), m.group(4)))
        if items:
            figs[fname] = items
    return figs


def check_figure_numbering(chapters, ws, fix):
    """图注编号：X 须等于章一级编号，Y 在章内按出现顺序连续（跨文件累计）。
    fix 时重编并同步正文引用。"""
    issues, applied = [], []
    figs = _figure_scan(chapters)
    # 章内序号按一级章分组、跨文件累计（2.3 与 2.4 同属第 2 章，序号接续）
    counters = {}
    for fname in sorted(figs, key=chapter_sort_key):
        cm = re.match(r"^(\d+)", fname)
        chap_no = cm.group(1) if cm else ""
        for (ln, raw, x, y, title, path) in figs[fname]:
            counters[chap_no] = counters.get(chap_no, 0) + 1
            want = f"图 {chap_no}-{counters[chap_no]}"
            got = f"图 {x}-{y}"
            if x != chap_no or y != counters[chap_no]:
                issues.append(f"{fname}: {got}（{title}）编号不规范，应为「{want}」")
                if fix:
                    fpath = ws / "chapters" / fname
                    text = fpath.read_text(encoding="utf-8")
                    # 替换图注行与正文引用（仅"图 X-Y"字符串映射，不碰其他内容）
                    text = re.sub(rf"图\s*{x}-{y}(?!\d)", want, text)
                    fpath.write_text(text, encoding="utf-8")
                    applied.append(f"{fname}: {got} → {want}（图注与正文引用已同步）")
    return issues, applied


def check_image_path(chapters, ws, fix):
    """图片引用路径有效性。fix 时在 figures/ 下找同名/唯一相似名修正。"""
    issues, applied = [], []
    figdir = ws / "figures"
    available = {p.name: p for p in figdir.glob("*") if p.is_file()} if figdir.exists() else {}
    for fname, info in chapters.items():
        for m in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", info["text"]):
            rel = m.group(1)
            if (ws / rel).exists():
                continue
            name = Path(rel).name
            issues.append(f"{fname}: 图片路径失效 {rel}")
            if fix:
                if name in available:
                    newrel = f"figures/{name}"
                    fpath = ws / "chapters" / fname
                    text = fpath.read_text(encoding="utf-8").replace(f"]({rel})", f"]({newrel})")
                    fpath.write_text(text, encoding="utf-8")
                    applied.append(f"{fname}: {rel} → {newrel}")
                else:
                    # 唯一相似名（去扩展名后前缀匹配）
                    stem = Path(name).stem
                    cand = [n for n in available if n.startswith(stem) or stem.startswith(Path(n).stem)]
                    if len(cand) == 1:
                        newrel = f"figures/{cand[0]}"
                        fpath = ws / "chapters" / fname
                        text = fpath.read_text(encoding="utf-8").replace(f"]({rel})", f"]({newrel})")
                        fpath.write_text(text, encoding="utf-8")
                        applied.append(f"{fname}: {rel} → {newrel}（相似名匹配）")
                    else:
                        applied.append(f"{fname}: figures/ 下无匹配图片，未修（需人工）")
    return issues, applied


def backup_chapters(ws):
    """--fix 前备份 chapters/ 到 chapters.bak-<时间戳>/。已存在同名备份则跳过。"""
    src = ws / "chapters"
    dst = ws / ("chapters.bak-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    shutil.copytree(src, dst)
    return dst


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser()
    ap.add_argument("-d", "--dir", required=True, help="项目工作区目录")
    ap.add_argument("--fix", action="store_true", help="自动修复低风险项（修复前自动备份 chapters/）")
    args = ap.parse_args()
    ws = Path(args.dir)

    tender = yaml.safe_load((ws / "tender-analysis.yaml").read_text(encoding="utf-8")) or {}
    outline = yaml.safe_load((ws / "bid-outline.yaml").read_text(encoding="utf-8")) or {}
    chapters = read_chapters(ws / "chapters")

    tech_items = [it for it in (tender.get("scoring") or {}).get("items", []) if it.get("category") == "技术"]
    tech_reqs = tender.get("tech_requirements") or []
    disq = tender.get("disqualification") or []
    o_chapters = flatten(outline.get("chapters"))

    # ── 1. 评分点覆盖矩阵 ──
    print("## 一、评分点覆盖率矩阵\n")
    print("| 评分项 | 分值 | outline挂章 | 章节文件 | 覆盖状态 |")
    print("|---|---|---|---|---|")
    score_to_chap = {}
    for ch in o_chapters:
        for s in ch.get("scoring") or []:
            score_to_chap.setdefault(str(s), []).append((str(ch.get("id")), ch.get("title")))
    file_scoring = {}
    for fname, info in chapters.items():
        for s in info["scoring"]:
            file_scoring.setdefault(s, []).append(fname)
    for it in tech_items:
        sid = str(it.get("id"))
        ochaps = score_to_chap.get(sid, [])
        ochap_str = "、".join(c[0] for c in ochaps) or "—"
        ffiles = file_scoring.get(sid, [])
        if ffiles:
            status = "✅ 已响应"
            fstr = "、".join(ffiles)
        elif ochaps:
            status = "⚠️ 章节在大纲但未生成/未声明"
            fstr = "—"
        else:
            status = "❌ 未挂章"
            fstr = "—"
        print(f"| {sid} {it.get('item')} | {it.get('score')} | {ochap_str} | {fstr} | {status} |")
    print()

    # ── 2. 技术需求关键词响应 ──
    print("## 二、技术需求逐条响应（关键词命中）\n")
    print("| id | 要求摘要 | 关键词 | 命中章节 | 状态 |")
    print("|---|---|---|---|---|")
    all_text = {fn: info["text"] for fn, info in chapters.items()}
    kw_map = {
        "97%": ["97%"], "10秒": ["10秒", "10 秒"], "400N": ["400N", "120kg", "120公斤"],
        "臂展": ["臂展"], "3000N": ["3000N"], "99%": ["99%"], "90天": ["90天", "90 天"],
        "台达": ["台达", "西门子", "施耐德"], "DCS": ["DCS", "某国产DCS品牌", "GN"],
        "对侧": ["对侧"], "专利": ["发明专利", "专利"], "24个月": ["24个月", "24 个月"],
        "偏离": ["偏离"], "RTX4060": ["RTX4060", "服务器"],
    }
    for tr in tech_reqs:
        tid = str(tr.get("id"))
        orig = (tr.get("requirement_original") or "").strip().replace("\n", " ")[:30]
        kws = []
        for k, v in kw_map.items():
            if k in (tr.get("requirement_original") or ""):
                kws = v
                break
        if not kws:
            kws = [orig[:6]]
        hits = [fn for fn, txt in all_text.items() if any(k in txt for k in kws)]
        status = "✅" if hits else "❌ 未命中"
        print(f"| {tid} | {orig}… | {'/'.join(kws[:2])} | {'、'.join(hits[:3]) or '—'} | {status} |")
    print()

    # ── 3. 废标风险（技术卷相关）──
    print("## 三、废标风险清单（技术卷相关）\n")
    print("| id | 条款摘要 | 自查结果 |")
    print("|---|---|---|")
    for d in disq:
        if d.get("manual"):
            continue
        did = str(d.get("id"))
        summ = (d.get("clause_original") or "").strip().replace("\n", " ")[:40]
        result = "需人工复核"
        if "偏离" in summ or "技术偏离" in summ:
            result = "✅ 技术偏离表已编制（零偏离）" if any("偏离" in t for t in all_text.values()) else "❌ 缺技术偏离表"
        elif "关键技术响应表" in summ:
            result = "✅ 关键技术响应表已编制" if any("关键技术响应表" in t for t in all_text.values()) else "❌ 缺关键技术响应表"
        print(f"| {did} | {summ}… | {result} |")
    print()

    # ── 4. 格式核对 ──
    print("## 四、格式核对\n")
    leaf = [c for c in o_chapters if not c.get("children")]
    print(f"- 大纲叶章节数：{len(leaf)}；章节 .md 文件数：{len(chapters)}")
    price_hits = []
    for fn, txt in all_text.items():
        for m in re.finditer(r"(报价|投标价|总价款|万元|人民币\s*\d)", txt):
            ctx = txt[max(0, m.start()-10):m.start()+12]
            if "报价" in m.group() or "人民币" in m.group():
                price_hits.append((fn, m.group(), ctx.replace("\n", " ")))
    if price_hits:
        print(f"- ⚠️ 检测到 {len(price_hits)} 处疑似报价/价格信息（技术卷禁含报价，需人工确认是否为违约条款语境）:")
        for fn, kw, ctx in price_hits[:10]:
            print(f"  - {fn}: 「…{ctx}…」")
    else:
        print("- ✅ 未检测到报价信息混入")
    print()

    # ── 5. 待补缺口 ──
    print("## 五、[待补]缺口清单\n")
    total_pending = 0
    for fn, info in chapters.items():
        if info["pending"]:
            total_pending += info["pending"]
            for m in re.findall(r"\[待补[^]]*\]", info["text"]):
                print(f"- **{fn}**：{m}")
    print(f"\n共 {total_pending} 个待补项。\n")

    # ── 6. 人工处理清单 ──
    print("## 六、人工处理清单（商务/价格/资质，技术卷范围外）\n")
    biz = [it for it in (tender.get("scoring") or {}).get("items", []) if it.get("category") in ("商务", "价格", "资质")]
    for it in biz:
        print(f"- [{it.get('category')}] {it.get('item')}（{it.get('score')}分）")
    manual_disq = [d for d in disq if d.get("manual")]
    for d in manual_disq:
        print(f"- [废标-人工] {(d.get('clause_original') or '').strip()[:40]}…")
    print()

    # ── 7. 低风险项自动修复（--fix）──
    print("## 七、低风险项检查与自动修复\n")
    checkers = [
        ("covers注释与大纲一致性", check_covers),
        ("文件名与大纲id一致性", check_filename_id),
        ("图注编号规范性", check_figure_numbering),
        ("图片引用路径有效性", check_image_path),
    ]
    all_issues = {}
    for name, fn in checkers:
        if fn in (check_covers, check_filename_id):
            issues, _ = fn(chapters, o_chapters, ws, fix=False)
        else:
            issues, _ = fn(chapters, ws, fix=False)
        all_issues[name] = issues
    total_issues = sum(len(v) for v in all_issues.values())

    if total_issues == 0:
        print("- ✅ 四项低风险检查全部通过，无需修复\n")
    else:
        for name, issues in all_issues.items():
            if issues:
                print(f"- **{name}**（{len(issues)} 项）:")
                for it in issues:
                    print(f"  - {it}")
        print()
        if args.fix:
            bak = backup_chapters(ws)
            print(f"已备份 chapters/ → {bak.name}/\n")
            print("修复记录：")
            chapters = read_chapters(ws / "chapters")  # 重读（修复可能改了文件）
            for name, fn in checkers:
                if not all_issues[name]:
                    continue
                if fn in (check_covers, check_filename_id):
                    _, applied = fn(chapters, o_chapters, ws, fix=True)
                else:
                    _, applied = fn(chapters, ws, fix=True)
                for a in applied:
                    print(f"  - {a}")
            print()
        else:
            print("（未开启 --fix，以上仅检测报告未修改；加 --fix 自动修复，修复前自动备份）\n")


if __name__ == "__main__":
    main()
