"""检查环节样例 fixture（SAMPLE_MODE=true 时替换 LLM 调用）。

- 错别字：在文本中真实查找迷你投标文件埋的「按装」错别字（mini_files.py 刻意埋点），
  命中即返回该条——证据回验天然通过；未命中返回空（不编造证据）。
- 废标项：按条款文本关键词给确定性裁决（盖章类→needs_review、报价类/期限类→pass）。
- 逻辑谬误/评分：贴合迷你投标文件内容的确定性 canned 结果（quote 真实存在于文本）。
"""
from app.models.scoring_item import ScoringItemRow
from app.schemas.workbench_check import (
    DisqScanResult, DisqVerdict, LogicIssue, LogicScanResult, ScoreEstimateItem,
    ScoreEstimateResult, TypoHit, TypoScanResult,
)


def sample_typo_scan(chunk: str) -> TypoScanResult:
    if "按装" in chunk:
        idx = chunk.index("按装")
        span = chunk[max(0, idx - 6): idx + 8].replace("\n", " ")
        return TypoScanResult(typos=[
            TypoHit(original=span, corrected=span.replace("按装", "安装"), reason="「安装」误写为「按装」（同音笔误）"),
        ])
    return TypoScanResult(typos=[])


def sample_logic_scan(content: str) -> LogicScanResult:
    if "四个阶段" in content and "110 天" in content:
        quote = "项目实施分为设计、制造、按装调试、试运行四个阶段，总工期 110 天。"
        if quote not in content:
            # 迷你文件被润色过时退化为按关键词截取的真实片段
            idx = content.index("四个阶段")
            quote = content[max(0, idx - 12): idx + 30].replace("\n", " ")
        if quote in content:
            return LogicScanResult(issues=[
                LogicIssue(
                    quote=quote, issue_type="数据不一致",
                    analysis="实施章节称总工期 110 天，但未与投标函响应承诺及招标文件规定交货期（120 天）建立一致性说明，缺里程碑节点支撑",
                    suggestion="补充四阶段的里程碑节点表，并明确总工期 110 天相对招标交货期 120 天的裕量说明",
                ),
            ])
    return LogicScanResult(issues=[])


def sample_scoring(items: list[ScoringItemRow]) -> ScoreEstimateResult:
    """迷你投标文件的技术评分点估分（S1/S2/S3 有 canned 值，其余按保守比例）。"""
    canned: dict[str, tuple[float, str, str, str]] = {
        "S1": (8.0, "本方案面向翻车机自动摘钩作业场景……系统总体架构分为感知层、决策层与执行层",
               "总体方案有架构描述，但缺少与招标需求逐条对应的映射说明", "在 8.1 总体方案开头补一段与招标范围逐项对应的响应总述"),
        "S2": (15.0, "机器人单钩摘钩作业周期实测 42 秒（含定位、摘钩、复位），优于招标要求的 45 秒；重复定位精度±2mm，优于招标要求的±3mm",
               "", "★硬指标全部优于要求，建议在响应表后附实测报告编号便于评委查证"),
        "S3": (6.5, "项目实施分为设计、制造、按装调试、试运行四个阶段……现场安装调试配置 6 名技术人员",
               "有阶段划分与人员配置，但缺网络计划图、风险预案等评分标准点名的要素", "补充施工网络计划图与风险预案章节"),
    }
    estimates = []
    for it in items:
        if it.item_key in canned:
            score, ev, ded, adv = canned[it.item_key]
        else:
            score, ev, ded, adv = round(it.score * 0.7, 1), "", "样例模式：该评分点无 canned 估分，按保守比例", ""
        estimates.append(ScoreEstimateItem(
            item_key=it.item_key, estimated_score=score, evidence=ev,
            deduction_reasons=ded, improvement_advice=adv,
        ))
    return ScoreEstimateResult(estimates=estimates)


def sample_disq_scan(clauses: list[dict]) -> DisqScanResult:
    verdicts: list[DisqVerdict] = []
    for d in clauses:
        clause = (d.get("clause_original") or "")
        cid = d.get("id") or ""
        if any(k in clause for k in ("盖章", "公章", "签字", "签名", "原件", "份数", "密封")):
            verdicts.append(DisqVerdict(
                clause_id=cid, verdict="needs_review", evidence="",
                analysis="该条款涉及盖章/原件类要求，无法仅凭投标文件文本判断，需人工现场核查",
                suggestion="开标前人工核查投标函盖章与签字是否齐全",
            ))
        elif "报价" in clause or "价格" in clause:
            verdicts.append(DisqVerdict(
                clause_id=cid, verdict="pass", evidence="技术卷全文未出现报价金额",
                analysis="技术卷各章节未检出报价信息，符合技术卷禁报价格要求", suggestion="",
            ))
        else:
            verdicts.append(DisqVerdict(
                clause_id=cid, verdict="pass", evidence="投标函：愿以响应招标文件全部要求的方式投标",
                analysis="投标函已对招标文件全部要求作出响应承诺", suggestion="",
            ))
    return DisqScanResult(verdicts=verdicts)
