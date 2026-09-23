"""LLM 接入薄封装（Q19/Q26 定稿）。

- openai 官方 SDK，base_url 可指本地 vLLM/Ollama
- Pydantic 模型即契约：prompt 嵌 JSON schema，响应 model_validate 校验
- 校验失败把错误喂回去让模型自我修正，最多 max_retries 次
- 不用 response_format 参数（vLLM/Ollama 兼容性无保证）
- 环节级配置：layer 指定 parse/draft/copilot，解析顺序 显式参数 > llm.<layer>.* > 环节默认 > 全局 llm.*
"""
import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from app.core.config import settings

T = TypeVar("T", bound=BaseModel)

# 环节默认值：段落 Copilot 是交互式短调用，不能继承解析用的 900s 超时与 0.3 温度
_LAYER_DEFAULTS: dict[str, dict[str, Any]] = {
    "copilot": {"timeout_seconds": 120, "temperature": 0.2, "max_tokens": 4096},
}


class LLMError(Exception):
    """LLM 调用失败（网络/超时/重试耗尽）。"""


@dataclass(frozen=True)
class LLMCallConfig:
    base_url: str
    api_key: str
    model: str
    temperature: float
    max_tokens: int
    timeout_seconds: int
    max_retries: int


def resolve_llm(layer: str = "", **overrides: Any) -> LLMCallConfig:
    """取某环节的生效配置。layer 为空即全局；overrides 里为 None 的键视为未指定。"""
    g = settings.llm
    layer_cfg = getattr(g, layer, None) if layer else None
    defaults = _LAYER_DEFAULTS.get(layer, {})

    def pick(field: str) -> Any:
        if overrides.get(field) is not None:
            return overrides[field]
        if layer_cfg is not None and getattr(layer_cfg, field, None) is not None:
            return getattr(layer_cfg, field)
        if field in defaults:
            return defaults[field]
        return getattr(g, field)

    return LLMCallConfig(
        base_url=pick("base_url"),
        api_key=pick("api_key"),
        model=pick("model"),
        temperature=pick("temperature"),
        max_tokens=pick("max_tokens"),
        timeout_seconds=pick("timeout_seconds"),
        max_retries=g.max_retries,
    )


def _client(cfg: LLMCallConfig) -> OpenAI:
    if not cfg.api_key:
        raise LLMError("LLM API key 未配置（.env 里 LLM__API_KEY）")
    return OpenAI(
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        timeout=cfg.timeout_seconds,
        max_retries=2,  # SDK 层网络重试；语义重试由 chat_structured 自管
    )


def _strip_fence(content: str) -> str:
    """模型偶发用 ```json ... ``` 包裹输出。"""
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:]
        content = content.strip()
    return content


def _load_json_lenient(content: str) -> Any:
    """先整体解析；失败则截取首个 { 到末个 } 再试（小模型常带前导语/尾注）。"""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start >= 0 and end > start:
            return json.loads(content[start : end + 1])
        raise


def chat_structured(
    system_prompt: str,
    user_prompt: str,
    schema: type[T],
    *,
    layer: str = "",
    timeout: int | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> T:
    """结构化输出调用：schema 的 JSON Schema 嵌进 prompt，校验失败喂错重试。"""
    cfg = resolve_llm(layer, timeout_seconds=timeout, temperature=temperature, max_tokens=max_tokens)
    client = _client(cfg)
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)
    sys = (
        f"{system_prompt}\n\n"
        "你必须只输出一个符合以下 JSON Schema 的 JSON 对象，不要输出任何其他文字、"
        "不要用 markdown 代码块包裹：\n" + schema_json
    )
    messages = [
        {"role": "system", "content": sys},
        {"role": "user", "content": user_prompt},
    ]
    last_err: str = ""
    for attempt in range(1 + cfg.max_retries):
        try:
            resp = client.chat.completions.create(
                model=cfg.model,
                messages=messages,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
            )
        except Exception as e:
            raise LLMError(f"LLM 请求失败: {type(e).__name__}: {e}") from e
        content = _strip_fence((resp.choices[0].message.content or "").strip())
        try:
            return schema.model_validate(_load_json_lenient(content))
        except (ValidationError, json.JSONDecodeError) as e:
            last_err = str(e)
            if attempt < cfg.max_retries:
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": (
                        "你上次输出的 JSON 校验失败，错误如下：\n" + last_err +
                        "\n请修正后重新只输出 JSON 对象。"
                    ),
                })
    raise LLMError(f"LLM 输出结构化校验失败（重试 {cfg.max_retries} 次后仍不通过）: {last_err}")


def chat_text_stream(
    system_prompt: str,
    user_prompt: str,
    *,
    layer: str = "",
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> Iterator[str]:
    """纯文本流式调用：逐 chunk yield 文本片段，无 schema 校验（供 SSE 打字机）。

    - 与 chat_structured 同口径：resolve_llm 环节配置 + _client，网络异常包装 LLMError
    - choices 可能为空列表、delta.content 可能为 None（role/usage chunk），必须 guard
    - 不吞 GeneratorExit：它是 BaseException，天然穿透 except Exception，
      客户端断开时正常向上传播（调用方据此放弃落库）
    - 无喂错重试：纯文本无 schema 可校验，空输出兜底由上层调用方负责
    """
    cfg = resolve_llm(layer, temperature=temperature, max_tokens=max_tokens)
    client = _client(cfg)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    try:
        stream = client.chat.completions.create(
            model=cfg.model,
            messages=messages,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            stream=True,
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            content = chunk.choices[0].delta.content
            if content:
                yield content
    except Exception as e:
        raise LLMError(f"LLM 请求失败: {type(e).__name__}: {e}") from e
