"""素材入库服务：资信文件 docx → 结构化素材卡（移植 ingest.py 解析逻辑，落数据库）。

原则（skill 版继承）：只记录原始文件里有的信息，不编造；资格关键字段缺失标 [待补]；
每张卡记 source 可追溯；同名卡增量更新不重复建。
"""
import re
import sys
from pathlib import Path

from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from app.core.database import SessionLocal  # noqa: E402
from app.core.storage import storage  # noqa: E402
from app.models.material import Material  # noqa: E402

from scripts.extract_docx import extract_docx_lines  # noqa: E402


def _parse_tables(lines: list[str]) -> list[list[list[str]]]:
    """从 extract_docx 输出还原表格：连续的 [表格]...[/表格] 块，行按 ' | ' 切。"""
    tables, cur, in_tbl = [], [], False
    for line in lines:
        if line == "[表格]":
            in_tbl, cur = True, []
        elif line == "[/表格]":
            if cur:
                tables.append(cur)
            in_tbl = False
        elif in_tbl:
            cur.append([c.strip() for c in line.split(" | ")])
    return tables


def _classify_case(name: str) -> tuple[bool, list[str], bool]:
    """判断案例是否翻车机摘复钩同类、覆盖范围、是否含正钩（与 ingest.py 一致）。"""
    is_related = bool(re.search(r"翻车|摘钩|复钩|正钩|摘复|摘挂|车厢|摘管|风管", name))
    scope = []
    if re.search(r"摘钩|摘复|摘挂|解列", name):
        scope.append("摘钩")
    if re.search(r"复钩|摘复|复列", name):
        scope.append("复钩")
    if re.search(r"正钩|摘复正|扶正", name):
        scope.append("正钩")
    if re.search(r"风管|摘管", name):
        scope.append("摘风管")
    return is_related, scope, "正钩" in scope


def run_ingest(file_object_rel: str, original_name: str) -> dict:
    """解析一份资信文件并入库素材卡。返回入库摘要（新增/更新统计 + 资格缺口清单）。"""
    db: Session = SessionLocal()
    try:
        src = storage.abspath(file_object_rel)
        lines, _ = extract_docx_lines(str(src))
        tables = _parse_tables(lines)
        full = "\n".join(lines)

        stats = {"cases": 0, "people": 0, "awards": 0, "patents": 0, "intro": 0}
        gaps = []

        def upsert(mtype: str, name: str, summary: str, qual: dict, tags: list[str]) -> Material:
            """同名同类型卡增量更新（Q5：全局唯一，source 记录最新来源）。"""
            m = (
                db.query(Material)
                .filter(Material.type == mtype, Material.name == name)
                .first()
            )
            if m is None:
                m = Material(type=mtype, name=name)
            m.summary = summary
            m.qual_extra = qual
            m.tags = ",".join(tags)
            m.source = original_name
            db.add(m)
            db.flush()
            return m

        # 公司简介
        m = re.search(r"公司简介\s*\n(.{100,3000}?)\n#?\s*公司部分资质", full, re.S)
        if m:
            upsert("capability", "公司简介", m.group(1).strip(), {}, ["公司简介", "研发能力"])
            stats["intro"] = 1

        for tbl in tables:
            if not tbl:
                continue
            header = "|".join(tbl[0])
            body = tbl[1:]

            # 业绩表 → 案例卡
            if "项目名称" in header and ("合同签订单位" in header or "终端使用单位" in header):
                for c in body:
                    if len(c) < 5 or not c[1]:
                        continue
                    name, contractor, client, sign_date = c[1], c[2], c[3], c[4]
                    is_rel, scope, has_zheng = _classify_case(name)
                    if not is_rel:
                        continue
                    summary = f"{client} {name}（{sign_date} 签订，签订单位 {contractor}）"
                    upsert("case", f"{client}{name}", summary, {
                        "client": client, "contractor": contractor, "sign_date": sign_date,
                        "amount_wan": "[待补]", "scope": scope, "has_正钩": has_zheng,
                        "is_同类摘复钩": is_rel, "proof_status": "缺证明材料",
                    }, ["翻车机", "摘复钩"] + scope)
                    stats["cases"] += 1
                    if has_zheng:
                        gaps.append(f"案例「{name[:20]}」({client[:14]}) 缺金额与证明材料")

            # 核心人员表 → 人员卡
            elif "姓名" in header and "专业领域" in header:
                for c in body:
                    if len(c) < 4 or not c[0] or c[0] == "姓名":
                        continue
                    name, field, title, background = c[0], c[1], c[2], c[3]
                    upsert("person", name, f"{name}，{title}，{field}。{background}", {
                        "field": field, "title": title, "degree": "[待补]",
                        "background": background, "project_experience": "[待补]", "certs": "[待补]",
                    }, [t for t in ["电力", "机器人", "AI", "视觉", "自动化"] if t in field + background])
                    stats["people"] += 1
                    if "高级" in title:
                        gaps.append(f"人员「{name}」({title}) 缺学历/项目经历/证书")

            # 获奖表
            elif "奖项名称" in header or "获奖项目" in header:
                for c in body:
                    if len(c) < 3 or not c[1] or c[1].startswith(("1、", "2、", "3、")):
                        continue
                    upsert("credential", c[1], f"{c[1]}（{c[2]}，{c[3] if len(c) > 3 else ''}）",
                           {"project": c[2], "issuer": c[3] if len(c) > 3 else ""}, ["获奖"])
                    stats["awards"] += 1

            # 专利表 / 软著表
            elif "专利申请名称" in header or "软件著作登记名称" in header:
                is_soft = "软件著作" in header
                for c in body:
                    if len(c) < 2 or not c[1]:
                        continue
                    upsert("ip", c[1], f"{c[1]}（{'软著' if is_soft else '专利'}）",
                           {"ip_type": "软著" if is_soft else "专利"}, ["软著" if is_soft else "专利"])
                    stats["patents"] += 1

        db.commit()
        return {"stats": stats, "gaps": gaps, "source": original_name}
    finally:
        db.close()


def search_materials(db: Session, keywords: list[str], limit: int = 8) -> list[Material]:
    """按关键词检索素材卡。

    两级召回（起草场景实测修正）：
    1. 关键词命中（tags/name/summary 包含匹配）——精确召回
    2. 章节语义类型推断——章节标题含"人员/团队/组织"必带人员卡+公司简介，
       含"业绩/案例/实力/经验"必带案例卡+资质，含"研发/创新/专利"必带知识产权卡；
       保证这些章节永远有素材可引（人员/案例卡是标书高频引用源）
    """
    all_m = db.query(Material).all()
    scored = []
    for m in all_m:
        hay = f"{m.name} {m.summary} {m.tags}"
        score = sum(1 for k in keywords if k and k in hay)
        if score:
            scored.append((score, m))

    # 语义类型推断
    text = " ".join(keywords)
    type_boost: set[str] = set()
    if re.search(r"人员|团队|组织|资质|岗位|简历", text):
        type_boost.update(["person", "capability"])
    if re.search(r"业绩|案例|实力|经验|资信", text):
        type_boost.update(["case", "credential"])
    if re.search(r"研发|创新|专利|软著|知识产权|技术能力", text):
        type_boost.update(["ip", "capability"])
    boosted = [m for m in all_m if m.type in type_boost]

    # 合并去重：关键词命中优先；类型召回按 person > case > credential > capability > ip
    # 排序（起草引用频率），capability 类（公司简介等通用卡）永远排最后
    seen = {m.id for _, m in scored}
    merged = [m for _, m in sorted(scored, key=lambda x: -x[0])]
    type_order = {"person": 0, "case": 1, "credential": 2, "capability": 3, "ip": 4}
    boosted.sort(key=lambda m: type_order.get(m.type, 9))
    for m in boosted:
        if m.id not in seen:
            merged.append(m)
            seen.add(m.id)
    return merged[:limit]
