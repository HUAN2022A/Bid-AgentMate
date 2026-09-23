"""样例模式 fixture：无模型配置时演示完整流程（内容贴合淮北翻车机项目域）。

约定：fixture 返回的都是各环节 LLM 输出契约的实例（TenderAnalysisResult 等），
调用方（analyze/bid_parse/检查/评分服务）在 settings.sample_mode 时替换真实 LLM 调用。
"""
from app.schemas.analysis import (
    CommercialNote,
    DisqualificationClause,
    FormatRequirement,
    QualificationItem,
    Scoring,
    ScoringItem,
    StructureRequirement,
    TenderAnalysisResult,
    TechRequirement,
)


def sample_tender_analysis() -> TenderAnalysisResult:
    """招标解析固定结果（样例迷你招标文件的镜像，与 mini_files.py 内容对齐）。"""
    return TenderAnalysisResult(
        project_name="淮北国安电厂二期翻车机自动摘钩机器人系统（样例）",
        tender_no="JAHB-2026-SAMPLE",
        scoring=Scoring(
            mode="总分制",
            total=100,
            items=[
                ScoringItem(
                    id="S1", category="技术", item="总体方案与技术路线", score=10,
                    criteria_original="技术方案总体思路清晰，系统架构合理，与招标需求匹配，得 8-10 分；基本满足得 4-7 分。",
                    location="第三章 评标办法 表1", response_hint="给出系统总体架构图与技术路线说明",
                ),
                ScoringItem(
                    id="S2", category="技术", item="自动摘钩机器人技术性能", score=15,
                    criteria_original="摘钩机器人满足 ★作业周期≤45秒、定位精度≤±5mm，全部满足得 12-15 分；一项不满足得 0 分。",
                    location="第三章 评标办法 表1", response_hint="逐项对照 ★ 参数给出响应值与证明", note="★硬指标",
                ),
                ScoringItem(
                    id="S3", category="技术", item="实施方案与进度保障", score=8,
                    criteria_original="施工组织方案完善、进度计划合理、保障措施充分，得 6-8 分。",
                    location="第三章 评标办法 表1", response_hint="网络计划图+人员配置+风险预案",
                ),
                ScoringItem(
                    id="S4", category="商务", item="质保期与售后服务", score=5,
                    criteria_original="质保期满足 24 个月，售后响应≤4小时，得 4-5 分。",
                    location="第三章 评标办法 表1", response_hint="质保承诺函+售后网点",
                ),
                ScoringItem(
                    id="S5", category="价格", item="投标报价", score=30,
                    criteria_original="以有效投标中的最低评标价为基准价，基准价得满分，每高 1% 扣 0.5 分。",
                    location="第三章 评标办法 表2", note="价格分自动计算",
                ),
            ],
        ),
        tech_requirements=[
            TechRequirement(id="T1", star=True, requirement_original="★机器人单钩摘钩作业周期 ≤45 秒（含定位、摘钩、复位）", location="技术规范书 2.1"),
            TechRequirement(id="T2", star=True, requirement_original="★摘钩定位精度 ≤±5mm，重复定位精度 ≤±3mm", location="技术规范书 2.2"),
            TechRequirement(id="T3", star=False, requirement_original="设备工作环境温度 -25℃~50℃，防护等级 IP65", location="技术规范书 3.1"),
        ],
        qualification=[
            QualificationItem(id="Q1", requirement_original="投标人须具有近三年（2023 年起）至少 1 个翻车机或同类铁路装卸系统业绩"),
            QualificationItem(id="Q2", requirement_original="本项目不接受联合体投标"),
        ],
        commercial_notes=[
            CommercialNote(id="C1", requirement_original="付款方式：合同签订后预付 30%，到货验收后付 60%，质保期满付 10%"),
        ],
        disqualification=[
            DisqualificationClause(id="D1", clause_original="未按规定格式填写投标函或投标函未加盖法人公章的，其投标将被否决", applies_to="全局"),
            DisqualificationClause(id="D2", clause_original="投标文件载明的招标项目完成期限超过招标文件规定期限的，其投标将被否决", applies_to="全局"),
            DisqualificationClause(id="D3", clause_original="技术卷中出现报价信息的，其投标将被否决", applies_to="技术卷"),
        ],
        format_requirements=[
            FormatRequirement(id="F1", requirement_original="技术文件一式 4 份，正本 1 份副本 3 份，A4 双面打印"),
        ],
        structure_requirements=[
            StructureRequirement(id="R1", requirement_original="技术文件应包含：总体方案、设备技术性能、实施方案、质保与服务"),
        ],
    )
