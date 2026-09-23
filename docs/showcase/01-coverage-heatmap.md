# 评分覆盖热力图（覆盖矩阵）设计

> 产品理念：标书 = 逐点响应评分标准。本功能把「评分点 × 章节」的覆盖关系在自查中心（交付页）可视化为一张热力矩阵，起草者一眼看到哪个评分点没写到位、哪个章节白写了。

## 设计方案

### 1. 现状盘点（设计依据）

| 事实 | 出处 |
|---|---|
| 评分点表：item_key/item/score/category/criteria_original（逐字原文） | `backend/app/models/scoring_item.py:10-23` |
| 章节表：每叶章一行，`scoring_keys` 逗号分隔挂接（如 `S3,S4`） | `backend/app/models/chapter.py:29` |
| 当前正文 = `chapter_versions` 最新版本行 | `backend/app/services/check_service.py:23-41`（`_latest_contents`） |
| 既有关键词命中逻辑：数字+单位正则抽取、`kw in txt` 判定 | `backend/app/services/check_service.py:126-134`（★硬指标节，内联） |
| 既有挂接收集：从 `outline_snapshots.tree` 递归收集 scoring_keys + 子章前缀匹配 | `backend/app/services/check_service.py:95-108` |
| 大纲确认时叶节点 scoring_keys 固化进 chapters | `backend/app/services/draft_service.py:73` |
| 交付路由前缀 `/api/projects/{project_id}`，Pydantic Out 模型定义在文件头 | `backend/app/api/delivery.py:15-50` |
| 交付页结构：操作卡 → 自查结果卡 → 导出结果卡 | `frontend/src/pages/DeliveryPage.tsx:70-196` |
| API 客户端按业务段落组织类型与函数 | `frontend/src/api.ts:266-301`（交付段） |

**一个已知问题**（演示库实测）：`backend/data/dev.db` 中 `outline_snapshots` 为空表，`chapters` 只有 1 行（`3.1`，挂 `S3,S4`）。`run_check` 的挂接收集只读大纲快照，在该库上会得到空映射（所有技术评分点判「未挂章」），与 `chapters.scoring_keys` 的真实挂接相矛盾。本设计把挂接收集抽成公共函数时**合并两个来源**（chapters 优先、大纲快照补充父章挂接），矩阵与自查报告同时受益。

### 2. 覆盖判定模型（核心语义）

行 = 评分点（全部 category，按 item_key 排序），列 = 章节（按 `sort_order`），格 = 该评分点在该章节上的响应状态，三态：

- **covered（绿）**：证据到位——硬指标关键词全部命中；或（无硬指标关键词时退化为挂接判定）挂接本章且正文已起草。
- **partial（橙）**：有关系但差口气——挂接本章但正文未起草；挂接本章、有正文但硬指标关键词未全命中；或未挂接但命中了部分关键词（顺带响应了一半）。
- **none（灰）**：无关或无从谈起——未挂接、未命中；或该章无正文且无命中。

**硬指标关键词**复用自查报告 ★硬指标节既有正则（`check_service.py:128`）：`\d+(?:\.\d+)?\s*(?:%|N|kN|kg|秒|天|个月|年|MPa|kV|mm|m\b)`，如 `99.5%`、`400N`、`24个月`。命中 = 关键词（strip 后）在章节正文中出现，与 `check_service.py:131` 语义一致。

格级判定决策表（`kws` = 从 `criteria_original` 抽取的关键词，`linked` = 该评分点挂接的章节集合〔含父章展开到叶章〕，`content` = 该章最新正文）：

| kws | 挂接本列 | 有正文 | 命中数 | 格状态 | 证据文案 |
|---|---|---|---|---|---|
| 非空 | 任意 | 有 | = 全部 | covered | 首个命中关键词 + 上下文（前后各 15 字） |
| 非空 | 任意 | 有 | 1 ~ 部分 | partial | 命中关键词 + 上下文，注明「命中 x/y」 |
| 非空 | 是 | 有 | 0 | partial | 「挂接本章但未命中硬指标关键词：…」 |
| 非空 | 否 | 有 | 0 | none | 空 |
| 非空 | 任意 | 无 | — | none | 空 |
| 空 | 是 | 有 | — | covered（退化挂接判定） | 「无硬指标关键词，按挂接+正文判定」 |
| 空 | 是 | 无 | — | partial（挂了未起草） | 「已挂接本章，正文未起草」 |
| 空 | 否 | — | — | none | 空 |

行级状态 `row_status` = 该行所有格的最高档（covered > partial > none），行首整行灰 = 该评分点没人管。`summary` 按行级统计。

判定为纯函数计算，不落库、不建表、不动状态机——每次 GET 实时算（读 3 张表 + O(评分点×章节×关键词数) 次子串搜索，典型 30×40×3 ≈ 3600 次，秒级返回；演示库 4×1 无压力）。

### 3. 后端改动

#### 3.1 `check_service.py` 抽公共函数（唯一一份关键词/挂接逻辑）

在 `backend/app/services/check_service.py` 顶部新增模块级公共函数，`run_check` 与新 coverage 共用，**不许出现第二份**：

```python
HARD_KW_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|N|kN|kg|秒|天|个月|年|MPa|kV|mm|m\b)")

def extract_hard_keywords(text: str) -> list[str]:
    """原文 → 硬指标关键词（数字+单位片段）。无数字指标时返回空表（调用方决定退化策略）。"""

def find_keyword_hits(kws: list[str], content: str) -> list[str]:
    """返回在正文中出现的关键词子集（strip 后匹配，语义同原 ★硬指标节）。"""

def latest_contents(db: Session, project_id: int) -> dict[str, str]:
    """{chapter_key: 最新版本正文}。原 _latest_contents 改公开，run_check 同步引用。"""

def scoring_chapter_map(db: Session, project_id: int) -> dict[str, list[str]]:
    """{item_key: [chapter_key...]}（已展开到叶章）。
    来源合并：先解析 chapters.scoring_keys（逗号分隔，同 copilot_service.py:96 惯例），
    再合并大纲快照 tree 的挂接（含挂父章，按 k==o or k.startswith(o+".") 展开到叶章，
    语义同原 check_service.py:107-108）。"""
```

`run_check` 内两处改为调用公共函数（行为对齐，唯一变化是挂接来源合并后演示库不再误判「未挂章」）：
- ★硬指标节（:126-134）：`kws = extract_hard_keywords(orig)`，`if not kws: kws = [orig[:8]]` 的 tech_requirement 兜底**保留在调用处**（那是硬指标清单特有策略，不进公共函数），命中判定改 `find_keyword_hits(kws, txt)`。
- 覆盖矩阵节（:95-108）：删掉内联 `_collect` 闭包与子章匹配，改 `score_to_chap = scoring_chapter_map(db, project_id)`。

#### 3.2 `build_coverage()` 服务函数

新增于 `backend/app/services/check_service.py`（自查语义内聚，不新建 service 文件）：

```python
def build_coverage(db: Session, project_id: int) -> dict:
    """构建评分点×章节覆盖矩阵（只读实时计算，不落库）。
    返回 {chapters, items, summary}，结构见 §3.3 响应模型。"""
```

流程：查全部评分点（按 item_key 排序）→ `latest_contents` 取正文 → `scoring_chapter_map` 取挂接 → 每评分点 `extract_hard_keywords(criteria_original)` → 逐章按 §2 决策表定格态、拼证据 → 行级聚合 + summary。

#### 3.3 API：`GET /api/projects/{project_id}/coverage`

挂 `backend/app/api/delivery.py`（复用既有 prefix、`_get_project`、`get_current_user`，风格同 :88-97 的 `export_preview`）。**不设状态门**：只读可视化，未确认大纲/未起草也能看（前端空态兜底）；项目不存在仍 404。

```python
@router.get("/coverage", response_model=CoverageOut)
def coverage(project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """覆盖矩阵：行=评分点，列=章节，格=covered/partial/none + 证据摘要。实时计算。"""
    _get_project(db, project_id)
    return CoverageOut(**build_coverage(db, project_id))
```

响应 JSON 形状（`cells` 与 `chapters` 等长同序，前端按下标对齐列）：

```json
{
  "chapters": [
    { "chapter_key": "3.1", "title": "设备安装方案", "has_content": true, "word_count": 2345 }
  ],
  "items": [
    {
      "item_key": "S3",
      "item": "实施方案",
      "category": "技术",
      "score": 10.0,
      "criteria_brief": "实施方案完整、可行，进度安排合理，得 10 分；实施方案基本完整、进度安排基本合理，得 6 分；…",
      "linked_chapters": ["3.1"],
      "row_status": "partial",
      "cells": [
        {
          "chapter_key": "3.1",
          "status": "partial",
          "hit_keywords": ["10 分"],
          "evidence": "…进度安排合理，得 10 分；我方施工进度编排…"
        }
      ]
    }
  ],
  "summary": { "total": 4, "covered": 0, "partial": 2, "none": 2 }
}
```

Pydantic Out 模型（定义在 `delivery.py` 文件头，同既有 `ExportPreviewOut` 风格）：`CoverageChapterOut` / `CoverageCellOut`（status 用 `Literal["covered","partial","none"]`）/ `CoverageItemOut` / `CoverageOut`。字段细节：

- `criteria_brief`：后端截 `criteria_original` 前 80 字（单行化），Tooltip 直接用，避免整段原文进矩阵 payload。
- `hit_keywords`：命中的关键词列表（前端渲染成 Tag）；`evidence`：≤60 字证据摘要或状态说明，空串表示无信息。
- `score` 为 Float（scoring_item.py:18），前端显示去尾零。
- `summary` 为**行级**统计，对应 DeliveryPage 既有「技术评分点覆盖 x/y」口径的细化。

### 4. 前端改动

#### 4.1 API 客户端（`frontend/src/api.ts`）

在交付段（:266-301 之后）追加类型 `CoverageChapterOut` / `CoverageCellOut` / `CoverageItemOut` / `CoverageOut`（与后端模型逐字段对齐，status 用 `'covered' | 'partial' | 'none'`）与：

```ts
export const getCoverage = (id: number) => request<CoverageOut>(`/api/projects/${id}/coverage`)
```

#### 4.2 新组件 `frontend/src/components/coverage/CoverageMatrix.tsx`

单文件、默认导出 `CoverageMatrix({ pid }: { pid: number })`，内部自取数据（TanStack Query `queryKey: ['coverage', pid]`），文件内定义 `MatrixHeaderCell` 与 `MatrixCell` 两个内部子组件。不引任何图表库，网格用 div + CSS Grid：

```
Card（title="覆盖矩阵：评分点 × 章节"，extra=图例+刷新按钮+「只看技术类」开关）
 └─ 空态判断
 └─ <div overflow:auto; max-height:480px>（横向+纵向滚动容器）
     └─ <div display:grid; grid-template-columns: 280px repeat(n, 72px); min-width:max-content>
         ├─ 左上角表头格（sticky top+left, z-index 3）"评分点 ＼ 章节"
         ├─ 章节表头行（sticky top:0, z-index 2，白底）: chapter_key + 截断标题；
         │   未起草章节（has_content=false）文字 secondary + 小 Tag「未起草」；悬停 Tooltip 全名+字数
         ├─ 每评分点一行：
         │   ├─ 首列（sticky left:0, z-index 1，白底）：item_key + 名称(ellipsis) + 分值徽标(Tag "10分")
         │   │   + row_status 色点；linked_chapters 为空时加灰 Tag「未挂章」；
         │   │   悬停 Tooltip 展示 criteria_brief 全文
         │   └─ n 个格子（MatrixCell）：div 28px 高、圆角 2px，三态底色，悬停 Tooltip
         └─ </div>
```

**颜色**（antd 语义色常量，不引 cssinjs token 也行，但取值与 AntD6 对齐）：

| 状态 | 底色 | hover |
|---|---|---|
| covered | `#52c41a`（green-6） | `#389e0d` |
| partial | `#faad14`（gold-6） | `#d48806` |
| none | `#f5f5f5` + 1px 边框 `#f0f0f0` | `#e8e8e8` |

**格子 Tooltip**（antd `Tooltip`，`overlayStyle={{ maxWidth: 360 }}`）：

```
[S3 实施方案 · 10分] × [3.1 设备安装方案]
评分原文摘要：…criteria_brief…
状态：已覆盖（硬指标 2/2 命中） / 部分覆盖（命中 1/2） / 挂接本章但未命中硬指标关键词 / 无关
命中关键词：99.5%  400N（Tag 列表，无则省略）
证据：…上下文摘要…（无则省略）
```

**交互**：
- 「只看技术类」：`Checkbox`，`useMemo` 过滤 `items.filter(i => i.category === '技术')`，默认关（全量含商务/价格/资质，整行灰直观呈现「技术卷范围外」）。
- 图例：三个色点 + 文案（已覆盖 / 部分覆盖 / 未覆盖），放 Card `extra`。
- 刷新：`ReloadOutlined` 按钮 → `qc.invalidateQueries({ queryKey: ['coverage', pid] })`。
- 图例旁显示行级汇总 `已覆盖 7 / 部分 3 / 未覆盖 2`。

#### 4.3 接入 `frontend/src/pages/DeliveryPage.tsx`

在操作卡（:72-96）之后、「自查结果」卡（:142）之前插入 `<CoverageMatrix pid={pid} />` 一行；`doCheck` 成功后（:33 附近）追加 `qc.invalidateQueries({ queryKey: ['coverage', pid] })`，自查完矩阵即时刷新。

### 5. 边界情况

| 场景 | 表现 |
|---|---|
| 评分点未挂章（演示库 S5/S9 实况） | 整行灰、行首灰 Tag「未挂章」，Tooltip 提示去大纲页挂接 |
| 章节未起草（无版本行或正文为空） | 列头灰字 +「未起草」Tag；挂接它的格子橙（挂了没写，行动项），未挂接格子灰 |
| 评分点挂父章（大纲树挂非叶节点） | `scoring_chapter_map` 按 `k.startswith(o + ".")` 展开到叶章列 |
| 大纲快照缺失（演示库 `outline_snapshots` 空） | 挂接回退 `chapters.scoring_keys`，矩阵照常工作 |
| 挂接 key 无对应章节行（大纲改版后残留） | 以 chapters 表为准，该挂接不落列；行内其余格正常算，linked_chapters 原样返回供排查 |
| `criteria_original` 无数字指标 | 退化为挂接判定（见 §2 决策表后三行），Tooltip 注明判定方式 |
| 无章节（未确认大纲） | `chapters: []` → 前端 `Empty`「尚无章节，确认大纲并起草后可见覆盖矩阵」 |
| 无评分点（未完成解析） | `items: []` → 前端 `Empty`「尚无评分点，完成招标解析后可见」 |
| 「只看技术类」过滤后为空 | 前端 `Empty`「该分类下无评分点」 |
| 评分点远多于章节 / 章节很多 | 外层 div 横向 + 纵向滚动：首列 `sticky left:0`、表头 `sticky top:0`、左上角双向 sticky，`max-height:480px` + `min-width:max-content` |
| score 为小数（如 2.5） | 前端 `String(score).replace(/\.0$/, '')` 去尾零展示 |

## 执行方案

实施顺序（每步可独立验证，后端先行）：

1. **抽公共函数并回接 `run_check`**（`check_service.py`）
   验证：重构前先起基线——`cd backend && ../.venv/Scripts/python.exe -m uvicorn app.main:app --port 8001`，登录拿 JWT，`POST /api/projects/1/check` 下载报告留底；改完后重复同一请求，确认 200、报告六节齐全、★硬指标节结果与基线一致（唯一预期差异：挂接列从「—」变为 `3.1`，即演示库挂接修复）。验证完 kill 8001 进程。

2. **实现 `build_coverage()`**（`check_service.py`）
   验证：`cd backend && ../.venv/Scripts/python.exe -c "from app.core.database import SessionLocal; from app.services.check_service import build_coverage; ..."` 直连 dev.db 调用并打印，核对演示数据预期：S3/S4 行在 3.1 列非灰（有正文、视命中定绿/橙）、S5/S9 行全灰且 linked_chapters 为空、summary 计数与行级一致。

3. **加路由 `GET /api/projects/{project_id}/coverage`**（`delivery.py`）
   验证：起 8001，curl 冒烟（带 JWT）核对 JSON 形状与 §3.3 一致、404 分支（不存在项目）返回「项目不存在」；`GET /openapi.json` 确认 schema 生成。验证完 kill。

4. **前端 API 客户端**（`api.ts` 加类型 + `getCoverage`）
   验证：`cd frontend && npx tsc -b --noEmit` 零 error（或直接跑第 5 步的 build 覆盖）。

5. **前端矩阵组件与接入**（新建 `CoverageMatrix.tsx`，改 `DeliveryPage.tsx`）
   验证：`cd frontend && npm run build`（tsc+vite 零 error）+ `npm run lint`（oxlint 不新增告警）。

6. **端到端联调**
   验证：起 8001 + 前端 `npm run dev`（5173），项目 1 交付页核对：矩阵渲染、三态颜色、Tooltip 内容、「只看技术类」过滤、刷新按钮、空态分支（可用新建无章节项目验证）。完成 kill 8001。

7. **补写本文档「完成结果」**（实现阶段收尾时）。

## 完成结果

（2026-09-11 实现阶段完成后补写）

### 做了什么

按 §3–§4 全量落地，改动 6 个文件（4 改 1 新建 + 本文档）：

- `backend/app/services/check_service.py`
  - 顶部抽出 4 个模块级公共函数：`HARD_KW_RE` + `extract_hard_keywords()` / `find_keyword_hits()` / `latest_contents()`（原 `_latest_contents` 改公开）/ `scoring_chapter_map()`（挂接合并两来源：chapters.scoring_keys 优先，再合并大纲快照 tree，挂父章按 `k == o or k.startswith(o + ".")` 展开到叶章列，去重）。
  - `run_check` 回接公共函数：★硬指标节改 `extract_hard_keywords` + `find_keyword_hits`（`orig[:8]` 兜底保留在调用处）；覆盖率矩阵节删内联 `_collect` 闭包与子章匹配，改 `scoring_chapter_map`（父章已在公共函数内展开，无须再匹配）。
  - 新增 `_clip()` / `_kw_evidence()`（关键词前后各 15 字上下文，单行化封顶 60 字）与 `build_coverage()`：查评分点（item_key 排序）→ 逐章按 §2 决策表定格态 → 行级聚合 + summary。
- `backend/app/api/delivery.py`：文件头新增 `CoverageStatus` Literal 别名与 `CoverageChapterOut` / `CoverageCellOut` / `CoverageItemOut` / `CoverageSummaryOut` / `CoverageOut` 五个 Out 模型；挂 `GET /coverage` 路由（不设状态门，项目不存在 404）。
- `frontend/src/api.ts`：交付段追加 5 个 Coverage 类型（status 用 `'covered' | 'partial' | 'none'`）与 `getCoverage(id)`。
- `frontend/src/components/coverage/CoverageMatrix.tsx`（新建）：自取 `useQuery ['coverage', pid]`；div + CSS Grid（`280px repeat(n, 72px)`、`min-width: max-content`、外层 `overflow: auto; max-height: 480px`），首列 sticky left（z-1）、表头 sticky top（z-2）、左上角双向 sticky（z-3）；内部子组件 `MatrixHeaderCell`（未起草灰字 + 小 Tag「未起草」）与 `MatrixCell`（三态底色 + hover 加深 + Tooltip 含评分原文摘要/状态/命中关键词 Tag/证据）；Card extra = 三色图例 + 行级汇总计数 + 「只看技术类」Checkbox + ReloadOutlined 刷新；三分支空态（无章节 / 无评分点 / 过滤后为空）。
- `frontend/src/pages/DeliveryPage.tsx`：操作卡与自查结果卡之间插入 `<CoverageMatrix pid={pid} />`；`doCheck` 成功后追加 `qc.invalidateQueries({ queryKey: ['coverage', pid] })` 联动刷新。

### 关键偏差（均为最小化处理）

1. **Pydantic 模型多一个**：设计命名 4 个 Out 模型，实现另加 `CoverageSummaryOut` 承载行级 summary 的四个 int 字段（`CoverageOut.summary` 引用它），前端同名接口类型对齐。
2. **Tooltip 状态行文案为前端推导**：响应不含「关键词总数」，格级 Tooltip 的「已覆盖（硬指标 x/x 命中）」由 covered 语义推导（x = hit_keywords.length），「部分覆盖（命中 x 项…）」只报命中数不报分母，分母信息由 evidence 的「命中 x/y」补充（后端有总数）。未为此改 schema。
3. **「未起草」Tag 位置**：放在列头第一行 chapter_key 旁（而非截断标题之后），避免 72px 列宽内 Tag 被裁切；语义不变（灰字 + Tag）。
4. 其余无偏差：颜色取值、判定决策表、cells 等长同序、不落库不建表、不设状态门、criteria_brief 80 字、evidence 60 字、分值去尾零，均按设计。

### 验证命令与输出摘要

1. **后端编译与导入**：`cd backend && ../.venv/Scripts/python.exe -m compileall -q app` → 无输出（通过）；`../.venv/Scripts/python.exe -c "import app.main"` → `import app.main OK`。
2. **重构前后 /check 基线对比**（设计 §执行方案步骤 1）：起 8001，admin 登录拿 JWT。基线 `POST /api/projects/1/check` → `covered:0`，报告 S3/S4「— ❌ 未挂章」（演示库 outline_snapshots 为空所致）；改完重复同一请求 → `covered:2`，S3/S4「3.1 ✅ 已响应」。两份报告 diff **仅**第一节两行（挂接列 + 状态），★硬指标节及其余四节逐字一致（star_reqs:2, star_hit:0）——设计预期的唯一差异（挂接修复）精确出现。
3. **决策表分支**（合成 in-memory sqlite 直调 `build_coverage`）：全命中→covered、部分命中→partial（evidence「命中 1/2」）、挂接零命中→partial（「挂接本章但未命中硬指标关键词」）、未挂接有正文→none、挂接未起草（有 kws）→none、无 kws 挂接+正文→covered（退化判定）、无 kws 未挂接→none；父章挂接（快照挂"1"、叶章 1.1/1.2）正确展开为 `linked=['1.1','1.2']`。与 §2 八行决策表逐行吻合。
4. **接口冒烟**（8001 + admin JWT）：`GET /api/projects/1/coverage` → HTTP 200，行数=评分点数 4、列数=章节数 1、每行 cells 与 chapters 等长同序；matrix = S3/S4 covered、S5/S9 none，summary `{total:4, covered:2, partial:0, none:2}`；criteria_brief 正常截断。`GET /api/projects/999/coverage` → 404「项目不存在」；无 token → 401；`/openapi.json` 生成 5 个 Coverage 模型且 status/row_status 为三态 enum。
5. **前端构建与静态检查**：`cd frontend && npm run build`（tsc -b && vite build）→ 0 error（vite 的 chunk >500kB 提示为既有信息性输出，非 error）；`npm run lint` → `Found 4 warnings and 0 errors`，与改动前基线完全一致（4 条均为 OutlinePage 既有 `set-state-in-effect`），未新增告警。
6. **清理**：8001 实例（PID 34832）已 taskkill，端口复验空闲；8000 全程未动。

### 遗留风险

- **浏览器端可视联调未执行**（设计 §执行方案步骤 6 的肉眼核对项）：本实现环境无浏览器，三态渲染、Tooltip、双向 sticky 滚动、「只看技术类」过滤与空态未经 5173 页面目视验证。组件已过 tsc 严格类型 + oxlint，接口形状经 curl 核对，但 UI 细节建议下次起 dev 环境时过一眼。
- 演示库 4 个评分点的 criteria_original 均不含数字+单位指标，全部走「退化挂接判定」分支；含硬指标的关键词命中路径已在合成库验证，但未在演示库真实数据上出现（演示数据本身限制，非实现缺口）。
- `build_coverage` 对每章额外查一次最新版本行取 word_count（N+1，与既有 `latest_contents` 同款模式），典型 40 章规模无压力；章节达数百时如变慢可改一次窗口查询。
- 挂接以 chapters 表叶章为准（设计 §5 既定行为）：大纲快照挂接的章节若已不在 chapters 表（大纲改版残留），该挂接静默不落列，仅 `linked_chapters` 原样返回供排查。
