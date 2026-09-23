"""章节路由：起草触发/章节列表/正文读写（版本快照 Q24）/版本历史/章节内 Copilot（Q8）。"""
import json
import time
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.llm import LLMError, chat_text_stream
from app.core.security import get_current_user
from app.models.chapter import Chapter, ChapterVersion
from app.models.project import Project
from app.models.user import User
from app.schemas.copilot import CopilotRequest, CopilotResponse
from app.services.copilot_service import (
    EMPTY_RETRY_SUFFIX,
    CopilotContext,
    finalize_copilot,
    prepare_copilot,
    run_copilot,
)
from app.services.draft_service import _save_version, dispatch_draft_all

router = APIRouter(prefix="/api/projects/{project_id}/chapters", tags=["chapters"])

# 人工保存时前端可声明的版本来源（Q24）：默认 human；应用 Copilot 结果后未再手改则为 ai_paragraph
SAVE_SOURCE_HINTS = {"human", "ai_paragraph"}


class ChapterOut(BaseModel):
    id: int
    chapter_key: str
    title: str
    target_words: int
    scoring_keys: str
    state: str
    draft_error: str
    needs_review: bool
    word_count: int  # 最新版本字数，0 = 未起草


class ChapterContentOut(BaseModel):
    id: int
    chapter_key: str
    title: str
    state: str
    scoring_keys: str
    content_md: str
    version_no: int
    word_count: int
    target_words: int


class ChapterSaveIn(BaseModel):
    content_md: str
    source_hint: str = "human"


class VersionOut(BaseModel):
    version_no: int
    source: str
    word_count: int
    created_at: str


def _get_project(db: Session, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return p


def _latest_version(db: Session, chapter_id: int) -> ChapterVersion | None:
    return (
        db.query(ChapterVersion)
        .filter(ChapterVersion.chapter_id == chapter_id)
        .order_by(ChapterVersion.version_no.desc())
        .first()
    )


@router.post("/draft-all")
def draft_all(
    project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """dispatcher 入口：全部 pending/draft_failed 章节逐章起草（Q22）。

    允许 outline_confirmed（首起草）/ draft_done（重跑失败）/ exported（补起草，如素材库更新后）。
    """
    p = _get_project(db, project_id)
    if p.state not in ("outline_confirmed", "draft_done", "exported"):
        raise HTTPException(status_code=409, detail=f"当前状态 {p.state} 不允许起草（须先确认大纲）")
    if p.state in ("draft_done", "exported"):  # 回到 outline_confirmed 让 dispatcher 接管
        p.state = "outline_confirmed"
        db.commit()
    return dispatch_draft_all(project_id)


@router.get("", response_model=list[ChapterOut])
def list_chapters(
    project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    _get_project(db, project_id)
    chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id)
        .order_by(Chapter.sort_order)
        .all()
    )
    out = []
    for ch in chapters:
        v = _latest_version(db, ch.id)
        out.append(ChapterOut(
            id=ch.id, chapter_key=ch.chapter_key, title=ch.title,
            target_words=ch.target_words, scoring_keys=ch.scoring_keys,
            state=ch.state, draft_error=ch.draft_error, needs_review=ch.needs_review,
            word_count=v.word_count if v else 0,
        ))
    return out


@router.get("/{chapter_id}", response_model=ChapterContentOut)
def get_chapter(
    project_id: int, chapter_id: int, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_project(db, project_id)
    ch = db.get(Chapter, chapter_id)
    if ch is None or ch.project_id != project_id:
        raise HTTPException(status_code=404, detail="章节不存在")
    v = _latest_version(db, ch.id)
    return ChapterContentOut(
        id=ch.id, chapter_key=ch.chapter_key, title=ch.title, state=ch.state, scoring_keys=ch.scoring_keys,
        content_md=v.content_md if v else "", version_no=v.version_no if v else 0,
        word_count=v.word_count if v else 0, target_words=ch.target_words,
    )


@router.put("/{chapter_id}", response_model=ChapterContentOut)
def save_chapter(
    project_id: int, chapter_id: int, body: ChapterSaveIn,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """人工保存：新版本快照（source 默认 human，白名单接受 ai_paragraph），章节状态转 edited。"""
    _get_project(db, project_id)
    ch = db.get(Chapter, chapter_id)
    if ch is None or ch.project_id != project_id:
        raise HTTPException(status_code=404, detail="章节不存在")
    source = body.source_hint if body.source_hint in SAVE_SOURCE_HINTS else "human"
    v = _save_version(db, ch, body.content_md, source=source, user_id=user.id)
    ch.state = "edited"
    db.commit()
    return ChapterContentOut(
        id=ch.id, chapter_key=ch.chapter_key, title=ch.title, state=ch.state, scoring_keys=ch.scoring_keys,
        content_md=v.content_md, version_no=v.version_no,
        word_count=v.word_count, target_words=ch.target_words,
    )


@router.post("/{chapter_id}/copilot", response_model=CopilotResponse)
def copilot_generate(
    project_id: int, chapter_id: int, body: CopilotRequest,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """章节内 Copilot：段落级 AI 动作，同步返回（交互式短调用，不走任务队列）。

    只生成不落正文：前端预览后"应用"才替换选区，保存时以 source_hint=ai_paragraph 落版本。
    """
    _get_project(db, project_id)
    ch = db.get(Chapter, chapter_id)
    if ch is None or ch.project_id != project_id:
        raise HTTPException(status_code=404, detail="章节不存在")
    try:
        return run_copilot(db, project_id, ch, body, user.id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e))


def _sse(event: str, payload: dict) -> str:
    """SSE 帧序列化：event + 单行 JSON data（json.dumps 已把换行转义，天然单行）。"""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _copilot_sse(
    db: Session, project_id: int, chapter: Chapter, req: CopilotRequest,
    user_id: int | None, ctx: CopilotContext,
) -> Iterator[str]:
    """SSE 生成器：delta* → done | error（互斥且必有其一，除非客户端断开）。

    - 契约类错误已在开流前抛 422（见 copilot_stream），HTTP 200 发出后一切错误只走 error 事件
    - 客户端断开 → yield 处收到 GeneratorExit（BaseException，穿透 except LLMError）→ 不做任何落库，
      半截输出不污染应用率统计；prepare 阶段 db.add 的素材快照随请求级 session 回滚
    - 心跳限制：同步生成器等 LLM 首 chunk 时阻塞、发不出心跳，兜底靠 copilot 层 120s 超时
    """
    yield ": stream-open\n\n"
    t0 = time.monotonic()
    chunks: list[str] = []
    llm_kwargs = {"max_tokens": ctx.spec.max_tokens, "timeout": ctx.spec.timeout_seconds}
    try:
        for delta in chat_text_stream(ctx.system_prompt, ctx.user_prompt, layer="copilot", **llm_kwargs):
            chunks.append(delta)
            yield _sse("delta", {"text": delta})
    except LLMError as e:
        yield _sse("error", {"message": str(e)})
        return  # 无有效输出，不落库
    content = "".join(chunks).strip()
    if not content:
        # 空输出兜底（对应非流式 min_length=1 → 喂错重试）：追加纠错指令整体重流一次，delta 照常外流
        retry_chunks: list[str] = []
        try:
            for delta in chat_text_stream(ctx.system_prompt, ctx.user_prompt + EMPTY_RETRY_SUFFIX, layer="copilot", **llm_kwargs):
                retry_chunks.append(delta)
                yield _sse("delta", {"text": delta})
            content = "".join(retry_chunks).strip()
        except LLMError as e:
            yield _sse("error", {"message": str(e)})
            return
    if not content:
        yield _sse("error", {"message": "模型输出为空（已纠错重试 1 次），请重试或换动作"})
        return
    latency_ms = int((time.monotonic() - t0) * 1000)  # 含空输出纠错重试全程，与非流式含喂错重试同口径
    try:
        resp = finalize_copilot(db, project_id, chapter, req, user_id, ctx, content, latency_ms)
    except Exception as e:  # 落库失败也必须给终止事件，不能让前端只看到"连接中断"
        yield _sse("error", {"message": f"结果落库失败: {type(e).__name__}: {e}"})
        return
    yield _sse("done", {
        "request_id": resp.request_id, "content_md": resp.content_md,
        "warnings": resp.warnings, "context_refs": resp.context_refs,
        "latency_ms": latency_ms,
    })


@router.post("/{chapter_id}/copilot/stream")
def copilot_stream(
    project_id: int, chapter_id: int, body: CopilotRequest,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """流式 Copilot：SSE 打字机（Q17 预留格式的落地）。上下文组装在开流前完成。

    prepare 同步组装上下文（404/422 在此发生，尚未开流）→ StreamingResponse 逐 delta 转发
    → finalize 软校验落库 → done。旧非流式端点 POST .../copilot 原样保留作兜底。
    生成器内落库用的是请求级 session：FastAPI 的 yield 依赖挂在 request 级退出栈，
    在响应发送完毕后才关闭。
    """
    _get_project(db, project_id)
    ch = db.get(Chapter, chapter_id)
    if ch is None or ch.project_id != project_id:
        raise HTTPException(status_code=404, detail="章节不存在")
    try:
        ctx = prepare_copilot(db, project_id, ch, body)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return StreamingResponse(
        _copilot_sse(db, project_id, ch, body, user.id, ctx),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{chapter_id}/versions", response_model=list[VersionOut])
def list_versions(
    project_id: int, chapter_id: int, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_project(db, project_id)
    ch = db.get(Chapter, chapter_id)
    if ch is None or ch.project_id != project_id:
        raise HTTPException(status_code=404, detail="章节不存在")
    versions = (
        db.query(ChapterVersion)
        .filter(ChapterVersion.chapter_id == chapter_id)
        .order_by(ChapterVersion.version_no.desc())
        .all()
    )
    return [
        VersionOut(
            version_no=v.version_no, source=v.source, word_count=v.word_count,
            created_at=v.created_at.isoformat() if v.created_at else "",
        )
        for v in versions
    ]
