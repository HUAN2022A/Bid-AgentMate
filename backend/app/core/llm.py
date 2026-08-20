"""LLM 接入薄封装（Q19/Q26 定稿）。

- openai 官方 SDK，base_url 可指本地 vLLM/Ollama
- Pydantic 模型即契约：prompt 嵌 JSON schema，响应 model_validate_json 校验
- 校验失败把错误喂回去让模型自我修正，最多 max_retries 次
- 不用 response_format 参数（vLLM/Ollama 兼容性无保证）
"""
import json
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from app.core.config import settings

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """LLM 调用失败（网络/超时/重试耗尽）。"""


def _client() -> OpenAI:
    if not settings.llm.api_key:
        raise LLMError("LLM API key 未配置（.env 里 LLM__API_KEY）")
    return OpenAI(
        base_url=settings.llm.base_url,
        api_key=settings.llm.api_key,
        timeout=settings.llm.timeout_seconds,
        max_retries=2,  # SDK 层网络重试；语义重试由 chat_structured 自管
    )


def chat_structured(system_prompt: str, user_prompt: str, schema: type[T]) -> T:
    """结构化输出调用：schema 的 JSON Schema 嵌进 prompt，校验失败喂错重试。"""
    client = _client()
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
    for attempt in range(1 + settings.llm.max_retries):
        try:
            resp = client.chat.completions.create(
                model=settings.llm.model,
                messages=messages,
                temperature=settings.llm.temperature,
                max_tokens=settings.llm.max_tokens,
            )
        except Exception as e:
            raise LLMError(f"LLM 请求失败: {type(e).__name__}: {e}") from e
        content = (resp.choices[0].message.content or "").strip()
        # 宽容剥离：模型偶发用 ```json ... ``` 包裹
        if content.startswith("```"):
            content = content.strip("`")
            if content.lower().startswith("json"):
                content = content[4:]
            content = content.strip()
        try:
            return schema.model_validate_json(content)
        except ValidationError as e:
            last_err = str(e)
            if attempt < settings.llm.max_retries:
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": (
                        "你上次输出的 JSON 校验失败，错误如下：\n" + last_err +
                        "\n请修正后重新只输出 JSON 对象。"
                    ),
                })
    raise LLMError(f"LLM 输出结构化校验失败（重试 {settings.llm.max_retries} 次后仍不通过）: {last_err}")
