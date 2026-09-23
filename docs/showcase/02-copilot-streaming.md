# 02 · 流式 Copilot（SSE 打字机）

> 对应设计决策 Q17（前后端实时通道：轮询起步，接口预留 SSE 兼容格式）。
> 现状：`POST /api/projects/{pid}/chapters/{cid}/copilot` 同步 JSON 一次性返回，长输出 5-40s 白屏等待。
> 目标：预览浮层里 AI 建议逐字流入（打字机），完成后无缝切到既有双栏 diff 对照视图；旧非流式端点原样保留作兜底。

## 设计方案

### 总体链路

```
选区 → CopilotBubble 触发动作
     → useCopilot.fire()：fetch POST /copilot/stream（带 Authorization 头）
     → 后端：prepare_copilot() 同步组装上下文（错误走 422，尚未开流）
     → StreamingResponse(text/event-stream)：chat_text_stream() 逐 chunk 转 event: delta
     → 前端 ReadableStream 手解 SSE：streaming 阶段浮层单栏打字机自动滚动
     → 流耗尽：finalize_copilot() 软校验 + 落 copilot_actions → event: done
     → 前端切 preview 阶段：既有双栏 diff + 溯源标签 + 软校验提示 + 应用/放弃/带指令重试
```

核心原则：**上下文配方与六条硬约束只有一份**。`copilot_service.py` 的 `SYSTEM_PROMPT_TEMPLATE`（含六条硬约束，`backend/app/services/copilot_service.py:82-92`）原样用于流式——流式只是不追加 `chat_structured` 的 JSON schema 尾巴，改为要求纯文本段落输出。

### 后端

#### 1. `backend/app/core/llm.py` 新增 `chat_text_stream()`

与 `chat_structured()`（`backend/app/core/llm.py:99-147`）平级的第二个入口，复用 `resolve_llm("copilot")`（环节配置：timeout 120s / temperature 0.2 / max_tokens 4096，`.env` 里 `LLM__COPILOT__MODEL=glm-5.3-flash`）与 `_client()`：

```python
def chat_text_stream(
    system_prompt: str, user_prompt: str, *,
    layer: str = "", temperature: float | None = None, max_tokens: int | None = None,
) -> Iterator[str]:
    """纯文本流式调用：逐 chunk yield 文本片段，无 schema 校验（供 SSE 打字机）。"""
```

要点：

- `client.chat.completions.create(..., stream=True)`，遍历 chunk，`chunk.choices[0].delta.content` 非空才 yield（choices 可能为空列表或 content 为 None，如 role/usage chunk，必须 guard）。
- 网络异常包装 `LLMError`，与 `chat_structured` 同口径；**不吞 GeneratorExit**——它是 BaseException，天然穿透 `except Exception`，客户端断开时正常向上传播（本 session 已实测装的 openai SDK 3.11.0 `chat.completions.create` 签名含 `stream`）。
- 无喂错重试（纯文本无 schema 可校验；空输出兜底由上层 copilot 生成器负责，见下）。

#### 2. `backend/app/services/copilot_service.py` 拆三段

现 `run_copilot()`（`backend/app/services/copilot_service.py:149-235`）是一条直线：组装 → LLM → 落库。拆成可复用的三段，非流式与流式共享首尾：

```python
@dataclass(frozen=True)
class CopilotContext:
    spec: ActionSpec          # 动作规格（含第 6 条硬约束）
    system_prompt: str        # SYSTEM_PROMPT_TEMPLATE.format(...)，流式非流式共用
    user_prompt: str          # 上下文 parts 拼接（章节信息/选区/前后文/评分点/技术要求/素材卡/指令）
    context_refs: dict        # {"scoring_keys": [], "material_ids": [], "requirement_key": ""}
    input_md: str             # 落库 input：selection 或技术要求原文

def prepare_copilot(db, project_id, chapter, req) -> CopilotContext:
    # 原 153-216 行原样搬入：spec 查表、选区校验、_load_scoring、素材检索 search_materials、
    # _snapshot_materials（只 db.add 不 commit，与非流式现状一致）
    # 契约类错误照旧抛 ValueError（API 层转 422）

def finalize_copilot(db, project_id, chapter, req, user_id, ctx, content, latency_ms) -> CopilotResponse:
    # 原 221-235 行原样搬入：_soft_check 软校验 → CopilotAction 落库 commit → CopilotResponse
    # model=resolve_llm("copilot").model 与非流式同源
```

`run_copilot()` 重排为串联（行为零变化，纯重构）：

```python
def run_copilot(db, project_id, chapter, req, user_id) -> CopilotResponse:
    ctx = prepare_copilot(db, project_id, chapter, req)
    t0 = time.monotonic()
    result = chat_structured(ctx.system_prompt, ctx.user_prompt, CopilotResult, layer="copilot")
    latency_ms = int((time.monotonic() - t0) * 1000)
    return finalize_copilot(db, project_id, chapter, req, user_id, ctx, result.content_md.strip(), latency_ms)
```

**latency 口径对齐**：两条路径的 `latency_ms` 都是"LLM 调用开始 → 输出完全到手"——非流式含喂错重试全程，流式含空输出纠错重试全程；都不含上下文组装。落库的 `model` 字段同走 `resolve_llm("copilot")`，copilot-stats 的 by_model 口径不受影响。

#### 3. `backend/app/api/chapters.py` 新增流式端点

```python
@router.post("/{chapter_id}/copilot/stream")
def copilot_stream(project_id, chapter_id, body: CopilotRequest, db, user):
    """流式 Copilot：SSE 打字机（Q17 预留格式的落地）。上下文组装在开流前完成。"""
```

路由前缀沿用现有 `router`（`backend/app/api/chapters.py:16`），完整路径 `POST /api/projects/{pid}/chapters/{cid}/copilot/stream`。与非流式端点（`chapters.py:152-170`）相同的 404/422 前置校验后：

1. **同步调 `prepare_copilot()`**：项目/章节不存在 → 404；`ValueError`（未选中、评分点缺失等契约错误）→ 422 JSON。**关键决策：契约类错误必须全部发生在开流之前**——HTTP 200 + `text/event-stream` 一旦发出，状态码不可再变，之后的一切错误只能走 `error` 事件。
2. 返回 `StreamingResponse(_generate(db, ctx, ...), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})`。无 GZip 中间件（`backend/app/main.py:30-36` 仅 CORS），uvicorn 对 StreamingResponse chunked 直发，无缓冲风险；`X-Accel-Buffering: no` 防将来挂反代时被缓冲，写上无害。
3. 端点保持 `def`（同步），FastAPI 对同步端点跑线程池；Starlette 对同步生成器自动 `iterate_in_threadpool`，不阻塞事件循环，与仓库现有全同步风格一致。
4. **db 会话存活到流结束**：FastAPI 依赖退出栈在响应发送完毕后才关闭，生成器内落库用的就是请求级 session（`get_db` yield 语义），无需自建 session。

#### 4. SSE 事件协议（逐字段）

帧格式遵循 SSE 标准：`event: <名称>\ndata: <单行 JSON>\n\n`，`data` 是 `json.dumps(..., ensure_ascii=False)`（换行已被 JSON 转义，天然单行）。流开始时先发一行注释 `: stream-open\n\n` 确认链路（客户端解析器忽略 `:` 开头行）。共三类事件：

**`delta` — 增量文本（0 到 N 次）**

| 字段 | 类型 | 说明 |
|---|---|---|
| `text` | string | 本次到达的增量片段（一个 LLM chunk 的 `delta.content`），前端按序拼接 |

**`done` — 生成完成（终止事件之一，恰好一次）**

| 字段 | 类型 | 说明 |
|---|---|---|
| `request_id` | number | `copilot_actions` 行 id，前端应用后打 applied 用（与非流式 `CopilotResponse.request_id` 同义） |
| `content_md` | string | 服务端 strip 后的权威全文。前端以此**覆盖**本地累积（消除首尾空白差异，丢包自愈） |
| `warnings` | string[] | 软校验结果（`_soft_check`：长度比/原样返回/疑似报价/[待补]） |
| `context_refs` | object | `{"scoring_keys": string[], "material_ids": number[], "requirement_key": string}`，与非流式同构，浮层溯源标签用它 |
| `latency_ms` | number | 诊断用（前端可展示"本次耗时"） |

**`error` — 失败（终止事件之一，恰好一次，其后流即关闭）**

| 字段 | 类型 | 说明 |
|---|---|---|
| `message` | string | 人类可读错误（LLM 网络失败 / 空输出重试仍空），前端 message.error 展示 |

事件顺序保证：`delta* → done` 或 `delta*（可为 0 次）→ error`；`done` 与 `error` 互斥且必有一个——除非客户端主动断开或网络中断（此时前端收不到终止事件，按"流中断"处理）。

#### 5. 生成器收尾与落库

```python
def _generate(db, project_id, chapter, req, user_id, ctx) -> Iterator[str]:
    yield ": stream-open\n\n"
    t0 = time.monotonic()
    chunks: list[str] = []
    try:
        for delta in chat_text_stream(ctx.system_prompt, ctx.user_prompt, layer="copilot"):
            chunks.append(delta)
            yield sse("delta", {"text": delta})
    except LLMError as e:
        yield sse("error", {"message": str(e)})
        return                                    # 不落库：无有效输出
    content = "".join(chunks).strip()
    if not content:
        # 空输出兜底（对应非流式 min_length=1 → 喂错重试）：追加修正指令重来一次，delta 继续外流
        chunks2: list[str] = []
        try:
            for delta in chat_text_stream(ctx.system_prompt,
                                          ctx.user_prompt + EMPTY_RETRY_SUFFIX, layer="copilot"):
                chunks2.append(delta)
                yield sse("delta", {"text": delta})
            content = "".join(chunks2).strip()
        except LLMError as e:
            yield sse("error", {"message": str(e)})
            return
    if not content:
        yield sse("error", {"message": "模型输出为空（已纠错重试 1 次），请重试或换动作"})
        return
    latency_ms = int((time.monotonic() - t0) * 1000)      # 含空输出重试全程
    resp = finalize_copilot(db, project_id, chapter, req, user_id, ctx, content, latency_ms)
    yield sse("done", {"request_id": resp.request_id, "content_md": resp.content_md,
                       "warnings": resp.warnings, "context_refs": resp.context_refs,
                       "latency_ms": latency_ms})
```

- **空输出兜底**：`EMPTY_RETRY_SUFFIX` 模仿 `chat_structured` 的喂错语义——在 user_prompt 尾部追加"你上次输出为空，请重新只输出处理后的这一段完整 markdown 正文"。重试的 delta 照常流出（前端打字机无感知）；重试仍空 → `error` 事件，**绝不空着 done、绝不落空行**。
- **取消路径（客户端断开）**：生成器在 yield 处收到 GeneratorExit，不做任何落库——半截输出不污染应用率统计（北极星指标）。与非流式的差异要明确：非流式前端 abort 后，后端照常跑完落一条"未应用孤儿行"（`frontend/src/api.ts:234` 注释即此语义）；流式取消则彻底无痕。`prepare` 阶段 `db.add` 的素材快照未 commit，随 session 回滚丢弃——`_snapshot_materials` 本就幂等（同项目同卡只存一次，`copilot_service.py:117-130`），下次生成重加，无害。
- **心跳限制（如实说明）**：同步生成器在等待 LLM 首 chunk 时阻塞，无法发心跳保活。LLM 流式通常首包 <2s，首包后 chunk 持续到达即是事实心跳；真正的兜底是 copilot 层 `timeout_seconds=120`（`llm.py:22`）。v1 接受此限制，不引入队列+线程的心跳架构。

### 前端

#### 6. `frontend/src/api.ts` 新增 `streamCopilot()`

EventSource 无法携带 `Authorization: Bearer` 头，必须 `fetch` + `resp.body.getReader()` 手解行协议：

```typescript
export async function streamCopilot(
  id: number, chapterId: number, body: CopilotRequest,
  handlers: { onDelta: (text: string) => void },
  signal?: AbortSignal,
): Promise<CopilotResponse>
```

- 复用 `getToken()` 组装 Authorization 头；`resp.status === 401` 走 `clearToken()` + 跳登录（与 `request()` 一致，`api.ts:23-44`）。
- 非 2xx：尝试 `resp.json()` 读 `detail` 抛 `ApiError`（SSE 端点开流前的 404/422 是普通 JSON 错误体，与现有错误通道同构）。
- 解析循环：`TextDecoder(stream: true)` 累积 buffer → 按 `\n\n` 切事件块 → 块内逐行：`:` 开头忽略（注释/心跳），`event: X` 记名，`data: Y` 取载荷（多行 data 按 SSE 规范以 `\n` join）。
- `delta` → `handlers.onDelta(JSON.parse(data).text)`；`done` → 存载荷；`error` → `throw new Error(message)`。
- reader 读尽（`done: true`）后：有 done 载荷 → 返回 `CopilotResponse`（字段结构与非流式完全一致，`api.ts:227-232`）；无 → `throw new Error('连接中断，请重试')`。
- abort：fetch signal 触发 reader 抛 AbortError 向上传播，由 useCopilot 的 aborted 分支静默处理。
- **404 自动降级**（兜底要求）：对旧后端（无 `/copilot/stream`）返回 404 时，调用方自动改走既有 `runCopilot()` 非流式路径。新前端 × 旧后端可用。

#### 7. `frontend/src/components/copilot/useCopilot.ts` 状态机

```
idle ──start()──> loading ──首个 delta──> streaming ──done──> preview
  ^                  │                        │                  │
  │                  │ 422/502/error事件       │ 停止（abort）      │ apply / discard
  └──────────────────┴────────────────────────┴──────────────────┘
                              preview ──retry(指令)──> loading（重新走全程）
```

改动点：

1. `CopilotPhase` 增加 `'streaming'`（`useCopilot.ts:40`）。
2. 新增 `streamText: string` state；`fire()` 进入时清空。
3. `fire()` 主体改为 `streamCopilot(pid, cid, body, { onDelta }, ac.signal)`：`onDelta` 里 `setStreamText(s => s + t)` 且若当前是 `loading` 则 `setPhase('streaming')`（首个 delta 即切换）。
4. done 载荷转 `result` → `setPhase('preview')`——`streamText` 此时仍留着，浮层渲染以 `result.content_md` 为准。
5. **降级分支**：catch 到 `ApiError` 且 `status === 404` → 改调 `runCopilot(...)`，phase 维持 `loading → preview` 老路径，UI 行为与今天完全一致。
6. elapsed 计时器条件扩为 `phase === 'loading' || phase === 'streaming'`（`useCopilot.ts:85-89`），打字机期间"已等待 Ns"仍可见。
7. `cancel/discard/apply/retry` 签名与语义零变化：`cancel` 在 streaming 阶段 abort → catch 里 `ac.signal.aborted` 分支静默回 idle；`retry` 从 preview 重新 `fire`（带新指令）。

#### 8. `frontend/src/components/copilot/CopilotPreview.tsx` 双形态

- props 增加 `phase: CopilotPhase`、`streamText: string`、`onCancel: () => void`；`open = phase === 'streaming' || phase === 'preview'`；空判从 `!job || !result`（`CopilotPreview.tsx:73`）放宽为 `!job`。
- **streaming 形态**：隐藏溯源标签（`context_refs` 尚未到达）、warnings、RetryBar；单栏 `Col span=24` 渲染 `streamText`（`whiteSpace: pre-wrap`，样式复用 `paneStyle`），尾部跟一个闪烁光标 span（CSS keyframes `@keyframes blink`，零依赖）；pane 容器挂 ref，`useEffect([streamText])` 里 `el.scrollTop = el.scrollHeight` 自动滚动；footer 只有一个"停止生成"按钮 → `onCancel`（即 abort）。
- **preview 形态**：现有双栏 diff（diff-match-patch 高亮）、溯源标签、软校验 Alert、应用/放弃/带指令重试**一概不动**——done 后 `result` 齐备，按既有渲染路径走。

#### 9. `frontend/src/pages/ChapterEditorPage.tsx` 接线

- `busy = copilot.phase !== 'idle'`（`ChapterEditorPage.tsx:159`）自动覆盖 streaming。
- 顶部等待 Alert 条件保持 `phase === 'loading'`（`ChapterEditorPage.tsx:188`）——streaming 起浮层接管，Alert 消失。
- `CopilotPreview` 多传 `phase/streamText/onCancel={copilot.cancel}`。

### 错误与取消路径全景

| 场景 | 后端行为 | 前端表现 |
|---|---|---|
| 未选中段落 / 评分点不存在等契约错误 | 开流前 422 JSON（`prepare_copilot` 抛 ValueError） | loading 阶段 catch → message.error → idle（与今天一致） |
| LLM 网络/超时/重试耗尽 | `error` 事件（HTTP 已 200） | streaming 阶段 throw → message.error → idle，半截 streamText 丢弃 |
| 空输出且纠错重试仍空 | `error` 事件，不落库 | 同上 |
| 用户点"取消"（loading）或"停止生成"（streaming） | 连接断开 → 生成器 GeneratorExit → 不落库、素材快照回滚 | abort 静默回 idle |
| 旧后端无流式端点 | 404 | 自动降级 `runCopilot` 非流式，行为与现状一致 |
| 401 登录失效 | 401 JSON | clearToken + 跳登录（复用既有通道） |
| 网络中断（无终止事件流断） | uvicorn 感知断连关闭生成器 | "连接中断，请重试" → idle |

### 明确不做

- 不加 `start`/`ping`/`meta` 事件类型（三类够用，Q17 兼容格式的最小集）。
- 不做流式中途的"边流边改"——输出契约仍是整段替换，应用语义不变。
- 不引入新 npm/pip 依赖（StreamingResponse 是 fastapi 自带；前端用浏览器原生 fetch/ReadableStream）。
- 不动旧端点 `POST .../copilot` 一行代码。

## 执行方案

分步实施，每步独立可验证。Python 一律 `backend` 目录下 `../.venv/Scripts/python.exe`；运行时验证自起 8001，**不动 8000**，验证完 kill 干净。

### 步骤 1：`llm.py` 增加 `chat_text_stream()`

改动：`backend/app/core/llm.py` 新增函数（复用 `resolve_llm`/`_client`，`Iterator[str]` 返回）。

验证（真实调智谱流式）：
```bash
cd backend && ../.venv/Scripts/python.exe -c "
from app.core.llm import chat_text_stream
chunks = list(chat_text_stream('你是中文编辑。', '用一句话介绍 SSE', layer='copilot'))
print(len(chunks), repr(''.join(chunks))[:120])"
```
通过标准：chunk 数 > 3（确为逐块而非一次到齐），拼接文本非空。

### 步骤 2：`copilot_service.py` 拆 `prepare_copilot` / `finalize_copilot`（纯重构）

改动：`backend/app/services/copilot_service.py`，`run_copilot` 改为三段串联，行为零变化。

验证（非流式回归）：
```bash
cd backend && ../.venv/Scripts/python.exe -m uvicorn app.main:app --port 8001
TOKEN=$(curl -s -X POST http://127.0.0.1:8001/api/auth/login -d 'username=admin&password=admin123' | ../.venv/Scripts/python.exe -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s -X POST http://127.0.0.1:8001/api/projects/1/chapters/1/copilot \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"action":"compress","selection_md":"本项目将组建专业实施团队，团队成员均具备丰富经验，能够保障项目顺利实施，确保各项任务按计划完成。"}'
```
通过标准：返回体仍含 `request_id/content_md/warnings/context_refs`；`data/dev.db` 的 `copilot_actions` 行数 +1 且 `model='glm-5.3-flash'`。

### 步骤 3：`chapters.py` 增加 `/copilot/stream` 端点 + SSE 生成器

改动：`backend/app/api/chapters.py`（新端点 + `_sse()` 帧序列化 + 生成器 + 空输出纠错重试）。

验证（8001 + curl -N 真实流式冒烟，核心闸门）：
```bash
curl -N -s -X POST http://127.0.0.1:8001/api/projects/1/chapters/1/copilot/stream \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"action":"rewrite","selection_md":"本项目将组建专业实施团队，团队成员均具备丰富经验。"}'
```
通过标准：
1. 多条 `event: delta` 分批陆续到达（肉眼可见时间差，非一次性倾泻）；
2. 终止事件为 `event: done`，data 含 `request_id`（数值）、`warnings`、`context_refs`、`content_md`；
3. `copilot_actions` 新行 latency/model 口径与非流式一致；
4. 反向用例：`selection_md` 传空 → 开流前即 HTTP 422 JSON（不是 SSE error）。

### 步骤 4：前端四文件改造

改动：`frontend/src/api.ts`（`streamCopilot` + SSE 行解析）、`frontend/src/components/copilot/useCopilot.ts`（streaming 阶段 + 降级）、`CopilotPreview.tsx`（双形态 + 自动滚动 + 光标）、`frontend/src/pages/ChapterEditorPage.tsx`（接线）。

验证（构建闸门）：
```bash
cd frontend && npm run build   # tsc + vite，必须零 error
cd frontend && npm run lint    # 新增代码零新增告警
```

### 步骤 5：端到端浏览器冒烟

`npm run build` 产物由 FastAPI 直托（`backend/app/main.py:71-83`），浏览器直接访问 `http://127.0.0.1:8001`（无需动 vite proxy，也别碰 8000）：admin/admin123 → 项目 1 → 章节 3.1 → 选中一段正文 → 浮动条"重写"。

通过标准：
1. 浮层立即出现，单栏打字机逐字流入并自动滚动（loading 期顶部仍有"已等待 Ns"Alert）；
2. done 后无缝切双栏 diff + 溯源标签 + 软校验提示；
3. "应用（替换选区）"写入编辑器、保存出现 `ai_paragraph` 版本；"带指令重试"回到 loading 重新流式；
4. 流式中途点"停止生成"立即回 idle，编辑器无残留；
5. Network 面板确认该请求为 fetch 流式响应（Event Type: eventstream 无 EventSource 实例）；
6. 降级：临时把 `streamCopilot` URL 改指向 8000（旧代码无 `/stream`，只发请求不重启它）→ 确认自动走非流式路径出结果 → 改回。

### 步骤 6：清理

kill 8001 进程（`netstat -ano | grep 8001` 找 PID → taskkill），确认端口释放；不执行任何 git commit/add。

## 完成结果

（2026-09-11 实现完成。改动文件：`backend/app/core/llm.py`、`backend/app/services/copilot_service.py`、`backend/app/api/chapters.py`、`frontend/src/api.ts`、`frontend/src/components/copilot/useCopilot.ts`、`frontend/src/components/copilot/CopilotPreview.tsx`、`frontend/src/pages/ChapterEditorPage.tsx`、本文档。）

### 做了什么

1. **`llm.py` 新增 `chat_text_stream()`**：与 `chat_structured` 平级的纯文本流式入口，复用 `resolve_llm`/`_client`，`stream=True` 逐 chunk yield（空 choices / None content 均 guard），网络异常包装 `LLMError`，不吞 `GeneratorExit`。
2. **`copilot_service.py` 拆三段（纯重构）**：新增 `CopilotContext` dataclass 与 `prepare_copilot()`（原组装段原样搬入，含素材快照 db.add）/ `finalize_copilot()`（原软校验+落库段原样搬入）；`run_copilot()` 改为 prepare → chat_structured → finalize 串联，行为零变化。新增 `EMPTY_RETRY_SUFFIX` 常量。
3. **`chapters.py` 新增 `POST /{chapter_id}/copilot/stream`**：`prepare_copilot` 同步前置（404/422 开流前抛出），`StreamingResponse` + `_copilot_sse` 生成器（`: stream-open` 注释帧 → delta\* → done|error），空输出兜底重流一次，`latency_ms` 含重试全程。`_sse()` 帧序列化用 `json.dumps(ensure_ascii=False)`。
4. **前端四文件**：`api.ts` 新增 `streamCopilot()`（fetch + ReadableStream 手解 SSE，`parseSseBlock` 行解析，401/非 2xx/读尽无 done 三类错误通道，`CopilotResponse` 增加可选 `latency_ms`）；`useCopilot.ts` 状态机扩 `streaming` 阶段 + `streamText` 累积 + 404 自动降级 `runCopilot`；`CopilotPreview.tsx` 双形态（streaming 单栏打字机 + 内联 keyframes 光标 + 自动滚动 + 停止生成按钮 / preview 双栏 diff 原样保留，主体抽为 `PreviewBody` 子组件）；`ChapterEditorPage.tsx` 接线多传 `phase/streamText/onCancel`。

### 关键偏差（及理由）

- **`finalize_copilot` 落库包了一层 `try/except Exception` → error 事件**（设计伪码无）：HTTP 200 已发出后若落库抛异常，裸传播会让流无终止事件、前端只能显示"连接中断"。包一层是守住"done 与 error 必有其一"的协议不变量，属最小防御性补充。
- **前端 `CopilotResponse` 增加可选 `latency_ms?`**：done 事件本就携带（设计第 4 节字段表），非流式端点无此字段；可选字段不破坏与非流式响应的结构一致性。
- **`CopilotPreview` 把 preview 主体抽成 `PreviewBody` 子组件**（设计未指定拆分）：空判放宽为 `!job` 后 TS 无法在 JSX 分支内收窄 `result`，抽子组件以 `result ?` 条件渲染收窄，同时保证 preview 形态渲染路径与原版逐行一致。
- **冒烟环境偏差（非设计偏差）**：Git Bash 控制台把含中文的 `-d` 请求体按本地码页编码导致 400，改用 python 写 UTF-8 JSON 体文件 + `curl --data-binary @file` 解决；与本功能代码无关。

### 验证命令与输出摘要

- **步骤 1（chat_text_stream 真实调智谱）**：`cd backend && ../.venv/Scripts/python.exe -c "...list(chat_text_stream('你是中文编辑。','用一句话介绍 SSE', layer='copilot'))..."` → `69 '**SSE（Server-Sent Events…` 69 个 chunk、拼接非空，确为逐块。
- **前端构建**：`cd frontend && npm run build` → tsc+vite 零 error（仅既有的 >500kB chunk 提示，与基线一致）。
- **前端 lint**：`cd frontend && npm run lint`（oxlint）→ `Found 4 warnings and 0 errors`，4 条告警文件与改动前基线完全相同（`markdown.ts:27`、`OutlinePage.tsx:66`、`ChapterEditorPage.tsx:74/102`），零新增。
- **后端**：`../.venv/Scripts/python.exe -m compileall -q app` → OK；`__import__('app.main')` → OK；openapi 含 `POST /api/projects/{project_id}/chapters/{chapter_id}/copilot/stream`。
- **流式冒烟（8001，curl -N）**：登录 admin → `curl -N -s -X POST .../chapters/1/copilot/stream --data-binary @body.json`（rewrite + 中文选区）逐行打时间戳：
  - **264 条 `event: delta` 分批陆续到达**（31.08s 首包 → 33.00s 末包，相邻事件间隔 20-60ms，非一次性倾泻；首包前 ~31s 为智谱长上下文 TTFT）；
  - 终止事件 `event: done`：data 含 `request_id=8`、`content_md`（全文 465 字）、`warnings`（长度比 + [待补] 两条）、`context_refs`、`latency_ms`；
  - 响应头 `Content-Type: text/event-stream`、`Cache-Control: no-cache`、`X-Accel-Buffering: no`、`Transfer-Encoding: chunked`；`: stream-open` 注释帧在首包前即到达（--max-time 3 掐断验证）；
  - **DB 断言**：`copilot_actions` 7 → 8 行，新行 `id=8 action=rewrite model=glm-5.3-flash latency_ms=33077 user_id=1`，input/output/warnings 落库正确；
  - **反向用例**：`selection_md=""` → 开流前 `HTTP 422 {"detail":"请先选中要处理的段落"}`（application/json，非 SSE error）；
  - **断开路径实证**：`--max-time 3` 掐断的流式请求（已发 stream-open、LLM 未返包）未新增任何 `copilot_actions` 行（计数仍 8），uvicorn 日志无异常栈——客户端断开 → 不落库成立。
- **非流式回归（纯重构行为校验）**：同一 body POST 旧端点 `.../copilot` → `request_id=9`、`warnings` 2 条、`context_refs` 三键齐备；DB 行 9 `model=glm-5.3-flash latency_ms=39875`。
- **清理**：`netstat -ano | grep 8001` 找 PID 18292 → `taskkill //F`，端口确认释放；8000（PID 29348）全程未动；未执行任何 git commit/add；临时 `smoke_body.json` 已删。

### 遗留风险

- **设计步骤 5 的浏览器端到端冒烟未执行**（本环境无浏览器）：打字机滚动、done 后切双栏 diff、"停止生成"即时回 idle、404 降级到旧后端等前端交互路径仅由 build/lint 与代码审查覆盖，待人工在浏览器走一遍。
- **长首包无心跳**（设计已如实声明）：实测智谱长上下文 TTFT 可达 ~30s，期间连接零数据流动；本地直连无碍，生产若挂反代/代理需调大空闲超时或后续版本加心跳架构。冒烟时 curl 经过了本机代理（响应含 `Proxy-Connection` 头）仍正常逐块到达，说明常见代理不缓冲 `text/event-stream`，但非保证。
- **降级判据是裸 404**：若将来给 `/copilot/stream` 加上业务性 404（如项目不存在），会误触发降级到旧端点再报一次错；当前两端点 404 语义一致，无误伤。
- **流式无 `CopilotResult` schema 约束**：非流式靠 min_length=1 喂错重试，流式只有"空输出重流一次"兜底；模型输出含解释性文字时流式无 JSON 校验拦截（六条硬约束第 4 条仅靠提示词），软校验（长度比/原样返回）仍会兜底提示。

