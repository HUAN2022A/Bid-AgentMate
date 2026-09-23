# 项目驾驶舱（Cockpit）设计

> 产品理念：投标文件是一条流水线——解析 → 大纲 → 起草 → 自查 → 导出。本功能为项目增加一页**只读总览**，把「项目走到哪一步、每章写到什么程度、评分分值怎么分布、还差多少字多少缺口」压缩成一屏，供负责人在起草期间高频查看，替代现在要在详情页/章节页/交付页三处来回切换的观测方式。

## 设计方案

### 1. 现状盘点（设计依据）

| 事实 | 出处 |
|---|---|
| 项目主状态机 9 态线性 + 唯一回退边 `parse_failed → parsing`（重新上传触发） | `backend/app/models/project.py:9-20`（`PROJECT_STATES`） |
| 前端已有状态中文标签 + AntD 色彩映射 `STATE_META` | `frontend/src/api.ts:522-532` |
| 章节状态机 `pending/drafting/draft_done/draft_failed/edited`，`needs_review` = 大纲变更标志 | `backend/app/models/chapter.py:24` 及 `:36` |
| 章节列表接口无状态门槛：`word_count` = 最新版本字数，0 = 未起草 | `backend/app/api/chapters.py:102-124`（`ChapterOut` 注释 `:39`） |
| 评分点接口无状态门槛：`scoring_items[]` 含 `category/score(float)/item` | `backend/app/api/outline.py:57-79`（`GET /api/projects/{id}/analysis`） |
| **导出预览有状态门槛**：仅 `draft_done/checking/exported` 允许，否则 409；且无正文章节时 409 | `backend/app/services/export_service.py:127-159`（门槛 `:132-133`，空正文 `:158-159`），路由 `backend/app/api/delivery.py:131-140` |
| 前端既有轮询范式：`refetchInterval` 按状态开关（详情页 3s / 章节页 3s） | `frontend/src/pages/ProjectDetailPage.tsx:15-16,33-34`、`frontend/src/pages/ChaptersPage.tsx:25,30` |
| 页面容器 maxWidth 1080、竖向 Space 16 布局 | `frontend/src/App.tsx:49` |
| 汇总数字的既有展示风格：`Row/Col + Statistic` | `frontend/src/pages/DeliveryPage.tsx:155-166` |
| 章节完成口径：`state ∈ {draft_done, edited}`；字数偏离告警阈值 0.8/1.2 | `frontend/src/pages/ChaptersPage.tsx:34`、`:130` |
| 演示数据实测（`backend/data/dev.db`）：项目 1 处于 `draft_done`，1 章 `edited`；评分点 4 个——商务 1 项 6 分、技术 3 项 23 分，总分 29 | 本设计阶段 sqlite 直查 |

**一个关键约束**：任务书建议汇总卡用 `GET /api/projects/{id}/export/preview`，但该接口在 `drafting` 期间返回 409（上表门槛）。而驾驶舱的主要使用期恰是起草期间。因此汇总卡不能整体依赖 preview——拆分数据来源见 §3.3，仍然零新端点。

### 2. 页面布局（版块位置与尺寸）

新路由 `/projects/:id/cockpit`，页面 `CockpitPage.tsx`，挂在既有 `RequireAuth` Shell 内（容器 1080px，`App.tsx:49`）。竖向三行，`Space size=16`：

```
┌────────────────────────────────────────────────────────────┐
│ ① 项目状态机流程条   Card 全宽，内容高约 96px                  │
│    created→parsing→outline_pending→outline_confirmed→        │
│    drafting→draft_done→checking→exported（8 节点横排）        │
├──────────────────────────────────────┬─────────────────────┤
│ ③ 汇总卡  Col span=14                │ ④ 评分分布 Col span=10│
│    Statistic ×3 横排                  │    分段条 + 类目明细   │
│    （总字数/目标、完成章、[待补]）       │    （竖排列表）        │
├──────────────────────────────────────┴─────────────────────┤
│ ② 章节进度列表  Card 全宽 Table                               │
│    编号/章节/状态徽标/字数进度条/needs_review/操作              │
└────────────────────────────────────────────────────────────┘
```

- 行 1 与行 2 用一个 `Row gutter={16}` 承载两个 `Col`（14/10），与行 1、行 3 之间 `Space` 间距。汇总卡三个 `Statistic` 横排约需 600px，span=14（约 613px）正好；评分分布的明细列表竖排，span=10（约 438px）足够。
- 章节表列最多（6 列），独占全宽行。
- 全页纯展示 + 跳转，无表单无写操作。

**入口**：项目详情页项目信息卡 `extra` 区（`frontend/src/pages/ProjectDetailPage.tsx:92-100`）加 `驾驶舱` 按钮（`DashboardOutlined`，`nav(\`/projects/${pid}/cockpit\`)`），**全状态可见**——created 态看流程图与引导空态，exported 态看终态回顾，都有观测价值。

### 3. 各版块设计（数据映射 / 空态 / 异常态）

#### 3.1 项目状态机流程条

- **节点**：主干 8 态（`PROJECT_STATES` 去掉 `parse_failed`），顺序即 `PROJECT_STATES` 顺序。每个节点 = 图标 + 中文标签（复用 `STATE_META` 标签），flex 横排 `space-between`，节点间箭头用 CSS 边框三角（不引库）。单节点含图标约 96px 高、8 节点 + 7 箭头在 1080px 内放得下；窄屏 `flex-wrap` 允许换行。
- **三态渲染**：按 `PROJECT_STATES.indexOf(state)` 求当前下标 `i`（`parse_failed` 映射到 `parsing` 下标但整条标红）：
  - 下标 < i：**已过态**——灰底 + 绿色 `CheckCircleFilled`，标签 secondary；
  - 下标 = i：**当前态**——主色描边高亮；若属 `{parsing, drafting, checking}` 加 `LoadingOutlined` 旋转图标；
  - 下标 > i：**未来态**——全灰 + `MoreOutlined`（或空心圆）。
  - `parse_failed`（当前态）：`parsing` 节点渲染红色 error 态，流程条下叠 `Alert type="error"` 显示 `project.parse_error`（与详情页 `ProjectDetailPage.tsx:107-114` 同源字段）。
- **交互**：当前态与已过态节点可点击，跳对应功能页——`outline_pending → /projects/:id/outline`，`outline_confirmed/drafting/draft_done → /projects/:id/chapters`，`checking/exported → /projects/:id/delivery`，`created/parsing/parse_failed → /projects/:id`。未来态不可点（灰、`cursor: default`）。
- **数据源**：`getProject(pid)`（`frontend/src/api.ts:101`，`GET /api/projects/{id}`）。

#### 3.2 章节进度列表

- **数据源**：`listChapters(pid)`（`frontend/src/api.ts:192`，`GET /api/projects/{id}/chapters`，无状态门槛）。
- **列**（对齐 `ChaptersPage.tsx:85-146` 但精简为总览视角）：
  | 列 | 宽 | 内容 |
  |---|---|---|
  | 编号 | 90 | `chapter_key` |
  | 章节 | auto | `title`；`needs_review` 时后缀橙色 Tag「大纲已变更」 |
  | 状态 | 110 | 徽标，复用章节状态映射（pending 待起草 / drafting 起草中 processing / draft_done 起草完成 cyan / draft_failed 起草失败 error / edited 已编辑 success，即 `ChaptersPage.tsx:8-14` 的 `CH_STATE_META`，提取为共享常量，见执行方案步骤 1）；`draft_failed` 行下追加 `draft_error` 一行红字（ellipsis + tooltip） |
  | 字数进度 | 240 | `Progress` 线形：`percent = min(100, round(word_count / target_words * 100))`，右侧文本 `word_count / target_words`；沿用 `ChaptersPage.tsx:130` 的偏离阈值——完成章超 120% 或有正文不足 80% 时 `status="warning"`；`draft_failed` 行 `status="exception"` |
  | 操作 | 80 | 「编辑」小按钮 → `/projects/:id/chapters/:chapterId` |
- **如实的进度语义**：起草中的章 `word_count` 为 0（`chapters.py:39` 注释「0 = 未起草」），进度条显示 0% 不跳动——只有该章完成后才有字数。列表头显示 `完成 x/y` 汇总（口径同 `ChaptersPage.tsx:34`）。
- **空态**：`locale.emptyText: '尚无章节（确认大纲后自动生成）'`（created/parsing/outline_pending 阶段 chapters 表为空，接口返回 `[]`）。

#### 3.3 汇总卡

三个 `Statistic`（风格对齐 `DeliveryPage.tsx:155-166`），**分项数据来源不同**，以消化 preview 的状态门槛：

| 指标 | 数据来源 | 口径说明 |
|---|---|---|
| 总字数/目标字数 | `listChapters` 前端聚合：`sum(word_count) / sum(target_words)` | 与 preview 的 `total_words` 口径一致：preview 亦为各章最新版本 `word_count` 之和（`export_service.py:143-157`，空白正文章不计——极罕见，视作等价）；差别是聚合版在 drafting 期间就实时可得 |
| 完成章节比 | `listChapters` 前端聚合：`state ∈ {draft_done, edited}` 计数 / 总数 | 与 `ChaptersPage.tsx:34` 完成口径一致 |
| [待补] 计数 | `state ∈ {draft_done, checking, exported}` 时才调用 `getExportPreview(pid)`（`frontend/src/api.ts:387`，`GET /api/projects/{id}/export/preview`）取 `pending_gaps` | 正文字段不在章节列表返回里，逐章数 `[待补` 只能读正文；drafting 期显示 `—` + Tooltip「全部章节起草完成后可统计」 |

- **TanStack Query 条件请求**：preview 查询用 `enabled: ['draft_done','checking','exported'].includes(project?.state ?? '')`，不满足时**不发请求**（而非发了 409 再吞掉）；项目轮询发现状态迁入 `draft_done` 时该查询自动启用。
- **空态**：无章节时总字数 `0 / 0`、完成 `0 / 0`；[待补] 按上表处理。

#### 3.4 评分分布

- **数据源**：`getAnalysis(pid)`（`frontend/src/api.ts:143`，`GET /api/projects/{id}/analysis`，无状态门槛，只读 `scoring_items` 表）。
- **聚合**（纯前端）：`Map<category, {count, sumScore}>`，`category` 空串归「未分类」；`score` 为 float（`outline.py:23`），显示时去尾零。
- **主视觉——分段条**（纯 CSS，不引图表库）：单条水平条高 28px、圆角，flex 子段 `width = sumScore / totalScore * 100%`，每段取 AntD 预设色板循环（blue/green/gold/purple/cyan/magenta…），段内白字「类目名 分值」，窄段 `text-overflow: ellipsis`。条上方标题行右侧标总分。
- **明细列表**：每类目一行——色点 + 类目名 + 分值（占比 %）+ 评分点数 n。
- **演示数据预期**：商务 6 分（1 项，21%）/ 技术 23 分（3 项，79%），总分 29。
- **空态**：`scoring_items` 为空（解析完成前）→ AntD `Empty`「解析完成后可查看评分分布」。

### 4. 轮询策略（何时开、何时停）

全部沿用既有 `refetchInterval` 按状态开关的范式（`ProjectDetailPage.tsx:33-34`），查询键复用既有键名使跨页缓存互通（`['project', pid]`、`['chapters', pid]`）：

| 查询 | 开（间隔） | 停 |
|---|---|---|
| `['project', pid]` | `state ∈ {parsing, drafting, checking}` → 5000ms | 其余状态返回 `false`（迁出即停，由刷新后的 project 数据驱动） |
| `['chapters', pid]` | `project.state === 'drafting'` → 5000ms（任务指定 5 秒；章节页是 3s，驾驶舱为只读总览可稍缓） | 其余状态 `false` |
| `['analysis', pid]` | 不轮询 | 评分点解析完成后不变，无轮询必要 |
| `['export-preview', pid]` | 不轮询；`enabled` 见 §3.3，进入可用状态时首次拉取 | 状态迁出可用集后 `enabled` 自动关闭 |

- **停的三重保障**：① 状态迁出（project 轮询的下一拍数据驱动 `refetchInterval` 回 `false`）；② 组件卸载（TanStack Query 自动清理定时器）；③ 浏览器后台标签页——`refetchIntervalInBackground` 用默认 `false`，后台自动暂停，回前台按 `refetchOnWindowFocus` 默认行为补一拍。
- **drafting → draft_done 迁移时的联动**：project 数据更新触发重渲染后，chapters 轮询自然停止，preview 查询 `enabled` 自然开启，无需手写 invalidate（数据都在同一次渲染里按新状态求值）。

### 5. API 复用决策：零新端点

四个版块共消费 4 个既有 GET：`/api/projects/{id}`、`/api/projects/{id}/chapters`、`/api/projects/{id}/analysis`、`/api/projects/{id}/export/preview`。**不新增任何后端端点、不改任何后端代码**。

不新增聚合端点（如 `GET /api/projects/{id}/cockpit`）的理由：章节级聚合（总字数/完成比）是 40 章规模的一次 `list` 前端求和，网络成本与后端聚合无差别；[待补] 的状态门槛是 preview 的**业务语义**（导出物视角），不是缺陷，驾驶舱以分档展示消化它，而非开后门。

### 6. 明确不做

- **不做写操作**：驾驶舱是只读观测页，起草/自查/导出入口仍在各自功能页（节点跳转提供入口）。
- **不引图表库**：分段条与流程条均为 CSS flex 实现（任务约束）。
- **不做 SSE 推送**：沿用仓库轮询范式（`ProjectDetailPage.tsx:15` 注释「Q17：轮询起步，接口预留 SSE 兼容」），将来全站统一升级时再改。
- **不展示覆盖矩阵**：那是交付页 `CoverageMatrix` 的职责（`frontend/src/components/coverage/`），驾驶舱不重复；需要时经状态节点跳转过去。

## 执行方案

### 步骤 1：`frontend/src/api.ts` 提取共享章节状态常量

把 `ChaptersPage.tsx:8-14` 的 `CH_STATE_META` 移入 `api.ts`（与 `STATE_META` 相邻，改名 `CHAPTER_STATE_META` 导出），`ChaptersPage.tsx` 改为 import。驾驶舱与章节页共用一份映射。

**验证**：`cd frontend && npm run build` → tsc 零 error；`npm run lint` 告警数不高于基线（当前基线 4 条既有告警）。

### 步骤 2：新建 `frontend/src/pages/CockpitPage.tsx`（核心步骤）

按 §2 布局实现四版块：状态机流程条（含 `parse_failed` 分支与节点跳转）、汇总卡（分档数据源 + preview `enabled` 条件请求）、评分分布（分段条 + 明细）、章节进度列表（6 列 Table）。四个 `useQuery` 按 §4 轮询表配置，查询键复用 `['project', pid]` / `['chapters', pid]`。全程只读 `api.ts` 既有函数，不新增 API 函数。

**验证**：`cd frontend && npm run build` 零 error；`npm run lint` 零新增告警。

### 步骤 3：接路由与入口

`frontend/src/App.tsx` 在 `:72` 旁注册 `<Route path="/projects/:id/cockpit" element={<CockpitPage />} />`；`ProjectDetailPage.tsx` 项目信息卡 `extra`（`:92-100`）加「驾驶舱」按钮。

**验证**：`npm run build` 零 error；浏览器直访 `/projects/1/cockpit` 刷新不 404（SPA 回退路由既有，`git log 5d8908b`）。

### 步骤 4：运行时冒烟（自起 8001，不动 8000）

```bash
cd backend && ../.venv/Scripts/python.exe -m uvicorn app.main:app --port 8001
```

- admin 登录拿 JWT 后 curl 四接口：`GET /api/projects/1`（`draft_done`）、`/api/projects/1/chapters`（1 章 edited）、`/api/projects/1/analysis`（4 评分点）、`/api/projects/1/export/preview`（200，含 pending_gaps）——核对驾驶舱各版块数据形状。
- `POST /api/projects` 造一个 `created` 态新项目，访问其驾驶舱：核对状态机流程条 created 高亮、章节表空态文案、评分分布空态、汇总卡 `0/0` 与 [待补] `—`。
- 浏览器（vite dev 或 build 后预览）核对：`draft_done` 项目四版块渲染、节点跳转、演示项目评分分布「商务 6 / 技术 23 / 总 29」。
- **轮询启停核对**：devtools Network 面板确认——`drafting` 项目下 chapters 请求每 5s 一发、project 离开 `drafting` 后停；后台标签页暂停。
- 验证完 kill 8001 进程并确认端口释放。

### 步骤 5：文档回填

实现完成后回写本文档「完成结果」节（实际偏差、验证命令与输出摘要、遗留风险），风格对齐 `docs/showcase/01`、`02`。

## 完成结果

（2026-09-11 实现阶段完成后补写）

### 做了什么

按 §1–§5 全量落地，改动 6 个文件（4 改 1 新建 + 本文档），**零新端点、未改任何后端代码**：

- `frontend/src/api.ts`：`STATE_META` 之后追加导出 `CHAPTER_STATE_META`（章节五态中文标签 + AntD 色彩，内容原样来自 `ChaptersPage.tsx:8-14`）。
- `frontend/src/pages/CockpitPage.tsx`（新建，核心）：四版块 + 四查询——
  - **流程条**：`FLOW_STATES` 主干 8 节点 flex `space-between` + CSS 边框三角箭头（窄屏 wrap）；三态渲染（已过 = 灰底 + 绿 `CheckCircleFilled`、当前 = 主色描边高亮 + `SPIN_STATES` 旋转 `LoadingOutlined`、未来 = 全灰 `MoreOutlined`）；`parse_failed` 映射 parsing 节点渲染 `current-failed` 红态 + 条下 `Alert type="error"` 展示 `parse_error`；当前/已过节点按 `STATE_ROUTE` 跳对应功能页，未来态 `cursor: default` 不可点。
  - **汇总卡**：`Statistic ×3`——总字数/目标与完成章节比由 `listChapters` 前端聚合，[待补] 仅 `PREVIEW_STATES ∈ {draft_done,checking,exported}` 时 `enabled` 条件请求 `getExportPreview` 取 `pending_gaps`（带值变色：>0 金 / =0 绿，对齐交付页），不满足显示 `—` + Tooltip「全部章节起草完成后可统计」。
  - **评分分布**：按 `category` 聚合（空归「未分类」、按分值降序），28px 圆角分段条（段宽 ∝ 分值占比、AntD 预设色板 6 色循环、白字 ellipsis + hover Tooltip 全量信息）+ 类目明细列表（色点 + 分值 + 占比 % + 项数），Card extra 标总分；空态 `Empty`「解析完成后可查看评分分布」。
  - **章节进度**：Table 5 列（编号 90 / 章节 + `needs_review` 橙 Tag / 状态徽标 + `draft_failed` 红字错误行 ellipsis+tooltip / 字数进度 240 = 线形 `Progress` + `word_count / target_words` 文本，偏离阈值 0.8/1.2 同章节页，`draft_failed` → `exception` / 编辑按钮 → 编辑器）；Card extra「完成 x/y」；空态文案「尚无章节（确认大纲后自动生成）」。
  - **轮询**：`['project',pid]` 在 `{parsing,drafting,checking}` 5s、`['chapters',pid]` 仅 `drafting` 5s（键名与详情页/章节页一致跨页互通）、`['analysis',pid]` 与 `['export-preview',pid]` 不轮询；`refetchIntervalInBackground` 默认 false 后台暂停，状态迁出由 project 轮询数据驱动自动停。
- `frontend/src/App.tsx`：注册 `Route path="/projects/:id/cockpit"`。
- `frontend/src/pages/ProjectDetailPage.tsx`：项目信息卡 `extra` 加「驾驶舱」按钮（`DashboardOutlined`，置于 extra 首位、全状态可见）。
- `frontend/src/pages/ChaptersPage.tsx`：删本地 `CH_STATE_META`，改 import 共享常量。

### 关键偏差（均为最小化处理）

1. **`Progress status="warning"` 在 antd v6 不存在**：`node_modules/antd/es/progress/progress.d.ts:23` 的 `ProgressStatuses` 仅 `normal/exception/active/success`。偏离告警改用 `strokeColor '#faad14'`（AntD warning 金）+ 字数文本 `type="warning"` 等价表达；`draft_failed` 仍按设计 `status="exception"`。视觉效果与 warning 态一致。
2. **章节表实现为 5 列而非「6 列」**：§2 布局图列了 6 项，但权威的 §3.2 列表把 `needs_review` 定义为「章节」列内后缀 Tag（非独立列），按 §3.2 实现（编号/章节/状态/字数进度/操作）；口径与 `ChaptersPage` 完全一致。
3. **流程卡 extra 加「返回详情」小按钮**：设计未提，但驾驶舱本身无返回路径（顶栏 logo 只回项目列表），加最小导航出口。另：当前态中的非进行态（created/outline_pending/outline_confirmed/draft_done/exported）图标设计未指定，补一个主色 CSS 圆点，语义中性。
4. **[待补] 请求在途显示 `—`**：`previewEnabled && 请求未返回` 时 `preview?.pending_gaps ?? '—'`，设计未指定在途态，取与不可用态相同的占位。

### 验证命令与输出摘要

1. **前端构建**：`cd frontend && npm run build`（tsc -b && vite build）→ **0 error**，产出 `dist/assets/index-*.js`（chunk >500kB 为既有信息性输出，非 error）。
2. **前端 lint**：`cd frontend && npm run lint` → `Found 4 warnings and 0 errors`，与实现前基线逐条一致（4 条均为 ChapterEditorPage/OutlinePage 既有告警），**零新增**。
3. **接口冒烟**（无新后端端点，按任务约定免 ③；为核对四版块数据形状自起 8001 做只读 curl，不写 dev.db）：
   - `cd backend && ../.venv/Scripts/python.exe -m uvicorn app.main:app --port 8001` → 启动成功；
   - `POST /api/auth/login`（admin）→ 200 拿 JWT；
   - `GET /api/projects/1` → `{"state":"draft_done",...,"parse_error":""}`；
   - `GET /api/projects/1/chapters` → 1 章 `[{"chapter_key":"3.1","state":"edited","word_count":1094,"target_words":2500,...}]`（汇总卡应为 总字数 1,094/2,500、完成 1/1）；
   - `GET /api/projects/1/analysis` → 4 评分点：技术 3 项 10+8+5=23、商务 1 项 6，与 §3.4 预期「技术 23（79%）/ 商务 6（21%）/ 总 29」吻合；
   - `GET /api/projects/1/export/preview` → HTTP 200 `{"total_words":1094,"pending_gaps":3,...}`——`total_words` 与 chapters 聚合 1094 逐字相等，实证汇总卡两口径等价；draft_done 态 [待补] 显示 3。
4. **清理**：8001 实例已 kill，复验 `netstat -ano | grep :8001` 仅 TIME_WAIT 残留、无 LISTENING；8000 全程未动。

### 遗留风险

- **浏览器可视联调未执行**（环境无浏览器，同 01 的限制）：流程条三态/parse_failed 标红、分段条渲染、节点跳转、轮询启停（devtools Network 的 5s 节奏与后台暂停）未经目视验证；已过 tsc 严格类型 + oxlint + 接口形状 curl 核对，建议下次起 dev 环境时过一眼。
- **created/drafting/parse_failed 态未经真实数据驱动渲染**：演示库仅 draft_done 项目，为免污染演示库未按设计步骤 4 造 created 新项目——这些分支为纯前端逻辑，靠代码审读保证。
- 分段条窄段（占比 <10%）段内文字 ellipsis，完整信息靠 hover Tooltip；类目 >6 时色板循环复用（相邻同色需 >12 类才会出现）。
- 汇总卡 [待补] 读 preview（导出物视角）而非自查报告，两者字段同源应恒等；如有分叉属后端既有逻辑，非本页引入。
